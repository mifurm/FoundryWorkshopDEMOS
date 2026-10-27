"""Custom agent code executed only inside the Foundry hosted sandbox."""

import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
from threading import Lock
from uuid import uuid4

from azure.ai.agentserver.invocations import InvocationAgentServerHost
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from opentelemetry import trace
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

app = InvocationAgentServerHost()
tracer = trace.get_tracer("workshop.hosted")
PRICE = 25
state_lock = Lock()


def validate_request(data: dict) -> int:
    attendees = data.get("attendees")
    if type(attendees) is not int or not 1 <= attendees <= 20:
        raise ValueError("attendees must be an integer from 1 to 20")
    return attendees


def quote(attendees: int) -> dict:
    return {
        "attendees": attendees, "currency": "USD", "unit_price": PRICE,
        "total": attendees * PRICE, "source": "hosted-read-only-price-tool",
    }


def advance_state(directory: Path, user_id: str) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{sha256(user_id.encode()).hexdigest()}.json"
    state = json.loads(path.read_text()) if path.exists() else {"visits": 0, "marker": uuid4().hex}
    state["visits"] += 1
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state))
    temporary.replace(path)
    return state


def execute(attendees: int, user_id: str) -> dict:
    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    with (
        DefaultAzureCredential() as credential,
        AIProjectClient(endpoint, credential, allow_preview=True) as project,
        project.get_openai_client() as client,
    ):
        model = os.environ["MODEL_DEPLOYMENT_NAME"]
        proposal = client.responses.create(
            model=model,
            input=f"Obtain the workshop price for exactly {attendees} attendees.",
            tools=[{
                "type": "function", "name": "lookup_price",
                "description": "Read the authoritative workshop price. No booking or payment.",
                "parameters": {
                    "type": "object", "properties": {"attendees": {"type": "integer"}},
                    "required": ["attendees"], "additionalProperties": False,
                },
                "strict": True,
            }],
            tool_choice={"type": "function", "name": "lookup_price"},
            parallel_tool_calls=False,
            max_output_tokens=600,
        )
        calls = [item for item in proposal.output if item.type == "function_call"]
        if proposal.status != "completed" or len(calls) != 1:
            raise RuntimeError("The live model did not produce exactly one completed pricing tool call.")
        call = calls[0]
        if call.name != "lookup_price" or json.loads(call.arguments) != {"attendees": attendees}:
            raise RuntimeError("The model proposed an unexpected tool or changed the attendee count.")
        with tracer.start_as_current_span("execute_tool lookup_price") as span:
            evidence = quote(attendees)
            span.set_attribute("workshop.attendees", attendees)
            span.set_attribute("workshop.total", evidence["total"])
        answer = client.responses.create(
            model=model,
            previous_response_id=proposal.id,
            input=[{"type": "function_call_output", "call_id": call.call_id,
                    "output": json.dumps(evidence)}],
            instructions="Return exactly the authoritative tool result fields. Do not book anything.",
            text={"format": {
                "type": "json_schema", "name": "verified_quote", "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "attendees": {"type": "integer"}, "currency": {"type": "string"},
                        "unit_price": {"type": "integer"}, "total": {"type": "integer"},
                        "source": {"type": "string"},
                    },
                    "required": ["attendees", "currency", "unit_price", "total", "source"],
                    "additionalProperties": False,
                },
            }},
            max_output_tokens=600,
        )
        if answer.status != "completed" or not answer.output_text:
            raise RuntimeError(f"Incomplete live model response: {answer.status}")
        parsed = json.loads(answer.output_text)
        if parsed != evidence:
            raise RuntimeError(f"Model answer failed the independent pricing check: {parsed}")
        with state_lock:
            state = advance_state(Path(os.environ.get("WORKSHOP_STATE_DIR", "/files/workshop")), user_id)
        return {
            "quote": parsed, "tool_result": evidence, "tool_call_id": call.call_id,
            "proposal_response_id": proposal.id, "response_id": answer.id,
            "usage": answer.usage.model_dump() if answer.usage else None,
            "state": state, "instance": socket.gethostname(),
            "agent_name": os.environ.get("FOUNDRY_AGENT_NAME"),
            "agent_version": os.environ.get("FOUNDRY_AGENT_VERSION"),
            "trace_id": format(trace.get_current_span().get_span_context().trace_id, "032x"),
        }


@app.invoke_handler
async def handle(request: Request) -> Response:
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        attendees = validate_request(data)
    except (ValueError, json.JSONDecodeError) as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise RuntimeError("Foundry did not supply the authenticated user ID; refusing unscoped state.")
    result = await asyncio.to_thread(execute, attendees, user_id)
    result["session_id"] = request.state.session_id
    return JSONResponse(result)


if __name__ == "__main__":
    app.run()
