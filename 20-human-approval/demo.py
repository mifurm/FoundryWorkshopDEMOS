"""Propose a synthetic booking; execute only an explicitly approved exact action."""

import argparse
import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from azure.identity import AzureCliCredential, get_bearer_token_provider
from openai import OpenAI


SCOPE = "https://cognitiveservices.azure.com/.default"
TOOL = {
    "type": "function",
    "function": {
        "name": "propose_booking",
        "description": "Propose, but never execute, a synthetic workshop booking.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "workshop": {"type": "string", "enum": ["foundry-intro"]},
                "attendees": {"type": "integer"},
            },
            "required": ["workshop", "attendees"],
            "additionalProperties": False,
        },
    },
}


def validate(action: dict) -> str:
    if set(action) != {"workshop", "attendees"}:
        raise ValueError("Only workshop and attendees are permitted.")
    if action["workshop"] != "foundry-intro":
        raise ValueError("Only the synthetic foundry-intro workshop is permitted.")
    if type(action["attendees"]) is not int or not 1 <= action["attendees"] <= 20:
        raise ValueError("Booking policy permits 1-20 attendees.")
    return json.dumps(action, sort_keys=True, separators=(",", ":"))


def actor_from_token(token: str) -> str:
    # Claims come only from the credential SDK's acquired token, never user input.
    segment = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    if not claims.get("oid") or not claims.get("tid"):
        raise ValueError("An Entra tenant/object identity is required.")
    return f'{claims["tid"]}/{claims["oid"]}'


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE IF NOT EXISTS proposals "
        "(id TEXT PRIMARY KEY, actor TEXT NOT NULL, payload TEXT NOT NULL, "
        "digest TEXT NOT NULL, expires REAL NOT NULL, status TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS bookings "
        "(proposal_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    db.commit()
    return db


def propose(db: sqlite3.Connection, actor: str, action: dict, now: float) -> tuple[str, str]:
    payload = validate(action)
    proposal_id = uuid4().hex
    digest = sha256(f"{proposal_id}:{actor}:{payload}".encode()).hexdigest()
    with db:
        db.execute("INSERT INTO proposals VALUES (?, ?, ?, ?, ?, 'pending')",
                   (proposal_id, actor, payload, digest, now + 900))
    return proposal_id, digest


def decide(
    db: sqlite3.Connection, actor: str, proposal_id: str, digest: str,
    approve: bool, now: float,
) -> str:
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT actor,payload,digest,expires,status FROM proposals WHERE id=?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Unknown proposal.")
        owner, payload, saved_digest, expires, status = row
        expected = sha256(f"{proposal_id}:{owner}:{payload}".encode()).hexdigest()
        if actor != owner or digest != saved_digest or expected != digest:
            raise PermissionError("Actor or exact approved action does not match.")
        validate(json.loads(payload))
        if status == "executed" and approve:
            booking = db.execute(
                "SELECT payload FROM bookings WHERE proposal_id=?", (proposal_id,),
            ).fetchone()
            if booking != (payload,):
                raise RuntimeError("Stored booking does not match the approved proposal.")
            return "REPLAY: existing synthetic booking; no duplicate created."
        if status != "pending" or now > expires:
            raise PermissionError("Proposal is rejected, completed, or expired.")
        if approve:
            db.execute("INSERT INTO bookings VALUES (?, ?)", (proposal_id, payload))
        db.execute("UPDATE proposals SET status=? WHERE id=?",
                   ("executed" if approve else "rejected", proposal_id))
        return "APPROVED: synthetic booking created." if approve else "REJECTED: no booking created."


def model_action(client: OpenAI, deployment: str, attendees: int) -> dict:
    response = client.chat.completions.create(
        model=deployment,
        messages=[
            {"role": "system", "content": "Propose the requested synthetic booking using the tool."},
            {"role": "user", "content": f"Book foundry-intro for {attendees} attendees."},
        ],
        tools=[TOOL],
        tool_choice={"type": "function", "function": {"name": "propose_booking"}},
        parallel_tool_calls=False,
        max_completion_tokens=200,
    )
    if len(response.choices) != 1:
        raise RuntimeError("Expected exactly one model choice.")
    choice = response.choices[0]
    calls = choice.message.tool_calls
    if (choice.finish_reason not in {"tool_calls", "stop"} or not calls or len(calls) != 1
            or getattr(choice.message, "refusal", None)):
        raise RuntimeError("Expected exactly one complete tool proposal.")
    call = calls[0]
    if call.type != "function" or call.function.name != "propose_booking":
        raise RuntimeError("Unexpected tool; no action executed.")
    action = json.loads(call.function.arguments)
    validate(action)
    if action["attendees"] != attendees:
        raise RuntimeError("Model proposal differs from the requested attendee count.")
    return action


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.getenv("AZURE_OPENAI_ENDPOINT"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"))
    parser.add_argument("--database", type=Path, default=Path(__file__).parent / ".azure/bookings.db")
    parser.add_argument("--attendees", type=int, default=4)
    decisions = parser.add_mutually_exclusive_group()
    decisions.add_argument("--approve", metavar="PROPOSAL_ID")
    decisions.add_argument("--reject", metavar="PROPOSAL_ID")
    parser.add_argument("--digest", help="Exact digest printed with the proposal.")
    args = parser.parse_args()
    if (args.approve or args.reject) and not args.digest:
        parser.error("Approval/rejection requires the exact --digest.")
    if not 1 <= args.attendees <= 20 or not args.deployment.strip():
        parser.error("Use 1-20 attendees and a nonempty deployment.")
    if not (args.approve or args.reject) and (
        not args.endpoint or not args.endpoint.startswith("https://")
    ):
        parser.error("Set AZURE_OPENAI_ENDPOINT to the Bicep endpoint output.")
    with AzureCliCredential() as credential:
        actor = actor_from_token(credential.get_token(SCOPE).token)
        db = connect(args.database)
        try:
            if args.approve or args.reject:
                print(decide(db, actor, args.approve or args.reject, args.digest,
                             bool(args.approve), time.time()))
                return
            with OpenAI(
                base_url=args.endpoint.rstrip("/") + "/openai/v1/",
                api_key=get_bearer_token_provider(credential, SCOPE),
                max_retries=0, timeout=45,
            ) as client:
                action = model_action(client, args.deployment, args.attendees)
            proposal_id, digest = propose(db, actor, action, time.time())
            print(f"PROPOSED ONLY (expires in 15 minutes): {json.dumps(action)}")
            print(f"Proposal: {proposal_id}\nDigest: {digest}")
            print(f"Approve: python demo.py --approve {proposal_id} --digest {digest}")
            print(f"Reject:  python demo.py --reject {proposal_id} --digest {digest}")
            print("No booking has been made. Review the exact action before approving.")
        finally:
            db.close()


if __name__ == "__main__":
    main()
