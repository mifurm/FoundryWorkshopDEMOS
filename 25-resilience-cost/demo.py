"""Bound retries, deadlines, concurrency, and costs without exhausting Azure quota."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import os
from threading import Lock
import time
from typing import Callable, TypeVar

from azure.identity import AzureCliCredential, get_bearer_token_provider
import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError


T = TypeVar("T")


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    deadline: float
    model_limit: int = 6
    tool_limit: int = 4
    model_requests: int = 0
    tool_calls: int = 0
    lock: Lock = field(default_factory=Lock, repr=False)

    def reserve(self, kind: str, clock: Callable[[], float] = time.monotonic) -> float:
        with self.lock:
            remaining = self.deadline - clock()
            if remaining <= 0:
                raise BudgetExceeded("Overall execution deadline reached.")
            if kind == "model":
                if self.model_requests >= self.model_limit:
                    raise BudgetExceeded("Model-request budget reached.")
                self.model_requests += 1
            elif kind == "tool":
                if self.tool_calls >= self.tool_limit:
                    raise BudgetExceeded("Tool-call budget reached.")
                self.tool_calls += 1
            else:
                raise ValueError("Unknown budget kind.")
            return min(remaining, 20.0)


def retry_delay(error: Exception, attempt: int, now: datetime | None = None) -> float:
    if isinstance(error, APIStatusError):
        header = error.response.headers.get("retry-after")
        if header:
            try:
                delay = float(header)
            except ValueError:
                delay = (parsedate_to_datetime(header) - (now or datetime.now(timezone.utc))).total_seconds()
            if not math.isfinite(delay) or delay < 0:
                raise ValueError("Invalid Retry-After header.")
            return delay
    return min(0.25 * 2 ** attempt, 4.0)


def bounded(
    operation: Callable[[float], T], budget: Budget, kind: str,
    attempts: int = 3, sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> T:
    if attempts < 1:
        raise ValueError("At least one attempt is required.")
    for attempt in range(attempts):
        timeout = budget.reserve(kind, clock)
        try:
            return operation(timeout)
        except (APIStatusError, APIConnectionError, TimeoutError, ConnectionError) as error:
            transient = (
                not isinstance(error, APIStatusError)
                or error.status_code in {408, 429, 500, 502, 503, 504}
            )
            if not transient or attempt == attempts - 1:
                raise
            delay = retry_delay(error, attempt)
            if delay >= budget.deadline - clock():
                raise BudgetExceeded("Retry-After/backoff exceeds the remaining deadline.") from error
            print(f"RETRY {kind}: {type(error).__name__}; wait {delay:.2f}s", flush=True)
            sleep(delay)
    raise AssertionError("Unreachable retry state.")


def synthetic_status(code: int, retry_after: str | None = None) -> APIStatusError:
    request = httpx.Request("POST", "https://synthetic.invalid/no-network-call")
    response = httpx.Response(
        code, request=request, headers={"Retry-After": retry_after} if retry_after else {},
    )
    error_type = RateLimitError if code == 429 else APIStatusError
    return error_type(f"Explicitly injected HTTP {code}; no Azure quota consumed.",
                      response=response, body=None)


class SyntheticBookingTool:
    """Local state only; retry after a lost response reuses the same booking key."""

    def __init__(self) -> None:
        self.bookings: dict[str, str] = {}
        self.response_lost = False

    def book(self, key: str, timeout: float) -> str:
        if timeout <= 0 or not key:
            raise ValueError("A deadline and idempotency key are required.")
        if key not in self.bookings:
            self.bookings[key] = "synthetic-booking-1"
        if not self.response_lost:
            self.response_lost = True
            raise TimeoutError("Injected lost tool response AFTER committing the booking.")
        return self.bookings[key]


def model_request(client: OpenAI, deployment: str, scenario: str, budget: Budget):
    attempts = 0

    def call(timeout: float):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            if scenario == "throttle":
                raise synthetic_status(429, "0.1")
            raise APITimeoutError(request=httpx.Request("POST", "https://synthetic.invalid"))
        return client.chat.completions.create(
            model=deployment,
            messages=[{"role": "user", "content": "Explain a workshop in one short sentence."}],
            max_completion_tokens=80,
            timeout=timeout,
        )

    response = bounded(call, budget, "model")
    if (not response.choices or response.choices[0].finish_reason != "stop"
            or not response.choices[0].message.content or response.usage is None):
        raise RuntimeError("Expected a complete answer and real token usage.")
    print(f"{scenario}: {response.choices[0].message.content}")
    return response.usage


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.getenv("AZURE_OPENAI_ENDPOINT"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"))
    parser.add_argument("--input-rate", type=float, default=0.40,
                        help="Illustrative USD per million input tokens; replace with your pricing.")
    parser.add_argument("--output-rate", type=float, default=1.60,
                        help="Illustrative USD per million output tokens; not a current price quote.")
    args = parser.parse_args()
    if not args.endpoint or not args.endpoint.startswith("https://") or not args.deployment.strip():
        parser.error("Set an HTTPS model endpoint and nonempty deployment.")
    if any(not math.isfinite(rate) or rate < 0 for rate in (args.input_rate, args.output_rate)):
        parser.error("Prices must be finite and nonnegative.")
    budget = Budget(deadline=time.monotonic() + 90)
    with AzureCliCredential() as credential, OpenAI(
        base_url=args.endpoint.rstrip("/") + "/openai/v1/",
        api_key=get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default"),
        timeout=20, max_retries=0,
    ) as client, ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(model_request, client, args.deployment, scenario, budget)
                   for scenario in ("throttle", "timeout")]
        usages = [future.result() for future in futures]
    tool = SyntheticBookingTool()
    booking = bounded(lambda timeout: tool.book("request-1", timeout), budget, "tool")
    print(f"Recovered write: {booking}; bookings={len(tool.bookings)} (must be 1)")
    if len(tool.bookings) != 1:
        raise RuntimeError("Duplicate booking created.")

    def outage(timeout: float) -> None:
        raise ConnectionError("Injected synthetic tool outage.")

    try:
        bounded(outage, budget, "tool")
    except BudgetExceeded as error:
        print(f"EXPECTED STOP: {error}; persistent outage did not loop indefinitely.")
    else:
        raise RuntimeError("The outage should have exhausted the tool budget.")

    def permanent(timeout: float) -> None:
        raise synthetic_status(403)

    try:
        bounded(permanent, budget, "model")
    except APIStatusError as error:
        if error.status_code != 403:
            raise
        print("EXPECTED PERMANENT FAILURE: HTTP 403 propagated without retry.")
    else:
        raise RuntimeError("Permanent failure was incorrectly swallowed.")
    try:
        bounded(lambda timeout: None, Budget(time.monotonic() - 1), "model")
    except BudgetExceeded as error:
        print(f"EXPECTED DEADLINE STOP: {error}")
    else:
        raise RuntimeError("Expired deadline was not enforced.")
    prompt_tokens = sum(usage.prompt_tokens for usage in usages)
    completion_tokens = sum(usage.completion_tokens for usage in usages)
    estimate = (prompt_tokens * args.input_rate + completion_tokens * args.output_rate) / 1_000_000
    print(f"Attempts: model={budget.model_requests}/{budget.model_limit}; "
          f"tool={budget.tool_calls}/{budget.tool_limit}; concurrency <= 2")
    print(f"Actual successful usage: input={prompt_tokens}; output={completion_tokens}")
    print(f"ILLUSTRATIVE estimate: USD {estimate:.6f}; assumed per-million rates "
          f"input={args.input_rate}, output={args.output_rate}. Not a billing report.")
    print("Injected faults never reached Azure. Budget alerts are not hard spending caps.")


if __name__ == "__main__":
    main()
