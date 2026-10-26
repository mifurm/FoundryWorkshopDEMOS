"""Export correlated, content-free model/tool spans to Azure Monitor."""

import argparse
import json
import os
from pathlib import Path
from time import sleep
from uuid import uuid4

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode


def pricing_tool(scenario, delay):
    if scenario == "slow":
        sleep(delay)
    if scenario == "failed":
        raise ValueError("Synthetic pricing service failure")
    return {"workshop": "intro", "price_usd": 25}


def run_agent(client, tracer, deployment, scenario, delay=2.0):
    request_id = uuid4().hex
    # Disable automatic exception events: SDK exceptions can contain request content.
    options = {"record_exception": False, "set_status_on_exception": False}
    with tracer.start_as_current_span("workshop-agent", attributes={
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "workshop-pricing",
        "workshop.request_id": request_id,
        "workshop.scenario": scenario,
    }, **options) as root:
        trace_id = f"{root.get_span_context().trace_id:032x}"
        print(json.dumps({"request_id": request_id, "trace_id": trace_id, "scenario": scenario}))
        with tracer.start_as_current_span("pricing-tool", attributes={
            "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "pricing",
        }, **options) as tool_span:
            try:
                result = pricing_tool(scenario, delay)
                tool_span.set_status(Status(StatusCode.OK))
            except ValueError:
                tool_span.set_attribute("error.type", "SyntheticToolFailure")
                # Azure Monitor reserves error.type instead of storing it as a custom dimension.
                tool_span.set_attribute("workshop.error_type", "SyntheticToolFailure")
                tool_span.set_status(Status(StatusCode.ERROR, "SyntheticToolFailure"))
                result = {"error": "pricing unavailable"}
        with tracer.start_as_current_span("chat workshop-summary", kind=SpanKind.CLIENT,
                                          attributes={
            "gen_ai.operation.name": "chat", "gen_ai.provider.name": "azure.ai.openai",
            "gen_ai.request.model": deployment,
        }, **options) as model_span:
            try:
                response = client.chat.completions.create(
                    model=deployment,
                    messages=[
                        {"role": "system", "content": "Summarize this synthetic pricing result in one sentence."},
                        {"role": "user", "content": json.dumps(result)},
                    ],
                    max_tokens=100, timeout=60,
                )
                model_span.set_attribute("gen_ai.response.id", response.id)
                if response.usage:
                    model_span.set_attribute("gen_ai.usage.input_tokens", response.usage.prompt_tokens)
                    model_span.set_attribute("gen_ai.usage.output_tokens", response.usage.completion_tokens)
                model_span.set_status(Status(StatusCode.OK))
            except Exception:
                model_span.set_attribute("error.type", "ModelRequestFailure")
                model_span.set_attribute("workshop.error_type", "ModelRequestFailure")
                model_span.set_status(Status(StatusCode.ERROR, "ModelRequestFailure"))
                root.set_status(Status(StatusCode.ERROR, "ModelRequestFailure"))
                raise RuntimeError(f"Model request failed; inspect trace {trace_id}") from None
        root.set_status(Status(StatusCode.OK))
        root.set_attribute("workshop.recovered_tool_failure", scenario == "failed")
    return trace_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.getenv("AZURE_OPENAI_ENDPOINT"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"))
    parser.add_argument("--connection-string", default=os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"))
    parser.add_argument("--slow-seconds", type=float, default=2.0)
    args = parser.parse_args()
    state = Path(__file__).parent / ".azure" / "deployment.json"
    if not args.connection_string and state.exists():
        args.connection_string = json.loads(state.read_text())["outputs"]["applicationInsightsConnectionString"]["value"]
    if not args.endpoint or not args.endpoint.startswith("https://") or not args.connection_string:
        parser.error("Set AZURE_OPENAI_ENDPOINT and APPLICATIONINSIGHTS_CONNECTION_STRING (or use deployment state).")
    if not 0 <= args.slow_seconds <= 30:
        parser.error("--slow-seconds must be between 0 and 30")
    from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter

    with DefaultAzureCredential() as credential:
        provider = TracerProvider(resource=Resource.create({"service.name": "lab18-workshop-agent"}))
        provider.add_span_processor(BatchSpanProcessor(AzureMonitorTraceExporter(
            connection_string=args.connection_string, credential=credential,
            disable_offline_storage=True, tracer_provider=provider,
        )))
        try:
            tracer = provider.get_tracer("lab18", "1.0")
            with AzureOpenAI(
                azure_endpoint=args.endpoint, api_version="2024-10-21",
                azure_ad_token_provider=get_bearer_token_provider(
                    credential, "https://cognitiveservices.azure.com/.default",
                ),
            ) as client:
                for scenario in ("normal", "slow", "failed"):
                    run_agent(client, tracer, args.deployment, scenario, args.slow_seconds)
            if not provider.force_flush(timeout_millis=30000):
                raise RuntimeError("Telemetry flush timed out")
            print("Spans flushed. Verify ingestion in Application Insights; export does not prove ingestion.")
        finally:
            provider.shutdown()


if __name__ == "__main__":
    main()
