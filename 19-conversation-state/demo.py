"""A real model tutor with scoped SQLite history and explicit truncation."""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4


SYSTEM = (
    "You are a concise workshop tutor. Only recall personal facts present in this conversation. "
    "If a prior fact is absent, say you do not know; do not invent memories. Answer in at most 30 words."
)


def cost(messages):
    """Conservative teaching bound, not exact billed tokens: UTF-8 bytes + 16/message."""
    return sum(len(message["content"].encode("utf-8")) + 16 for message in messages)


def bounded(history, prompt, budget, reserve):
    if not 1 <= reserve < budget:
        raise ValueError("Reserve must be positive and smaller than the context budget.")
    messages = [{"role": "system", "content": SYSTEM}]
    current = {"role": "user", "content": prompt}
    if cost(messages + [current]) + reserve > budget:
        raise ValueError("Current prompt cannot fit; shorten it or raise --budget.")
    retained = list(history)
    removed = 0
    while cost(messages + retained + [current]) + reserve > budget:
        retained = retained[2:]
        removed += 2
    return messages + retained + [current], retained, removed


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS conversations "
                "(user TEXT, conversation TEXT, history TEXT NOT NULL, "
                "PRIMARY KEY(user, conversation))"
            )
        self.path.chmod(0o600)

    def load(self, user, conversation):
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute("SELECT history FROM conversations WHERE user=? AND conversation=?",
                             (user, conversation)).fetchone()
        return json.loads(row[0]) if row else []

    def save(self, user, conversation, history):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR REPLACE INTO conversations VALUES (?, ?, ?)",
                       (user, conversation, json.dumps(history)))

    def delete(self, user, conversation):
        with closing(sqlite3.connect(self.path)) as db, db:
            cursor = db.execute("DELETE FROM conversations WHERE user=? AND conversation=?",
                                (user, conversation))
        return cursor.rowcount

    def reset(self, user, conversation):
        self.save(user, conversation, [])


def turn(store, client, deployment, user, conversation, prompt, budget=800, reserve=120):
    if not prompt.strip():
        raise ValueError("Prompt must not be empty.")
    messages, retained, removed = bounded(store.load(user, conversation), prompt, budget, reserve)
    response = client.chat.completions.create(
        model=deployment, messages=messages, max_completion_tokens=reserve,
        temperature=0, timeout=60,
    )
    choice = response.choices[0]
    if choice.finish_reason != "stop" or not choice.message.content:
        raise RuntimeError("Incomplete model answer; stored state was not changed.")
    answer = choice.message.content
    retained += [{"role": "user", "content": prompt}, {"role": "assistant", "content": answer}]
    # Persist only complete pairs that can be considered for the next turn.
    while retained and cost([{"role": "system", "content": SYSTEM}] + retained) + reserve > budget:
        retained = retained[2:]
        removed += 2
    store.save(user, conversation, retained)
    print(f"user={user} thread={conversation} input_bound={cost(messages)} "
          f"reserved_output_tokens={reserve} removed_messages={removed}\n{answer}")
    return answer


def demonstration(store, client, args):
    thread = f"lesson-{uuid4().hex[:12]}"
    print("Two logical application users, not an authentication demonstration.")
    try:
        turn(store, client, args.deployment, "alice", thread,
             "My notebook label is MAPLE-742. Acknowledge it.", args.budget, args.reserve)
        saved = store.load("alice", thread)
        if not saved:
            raise RuntimeError("Budget too small to retain the first turn; use --budget 800.")
        resumed = Store(store.path)
        if resumed.load("alice", thread) != saved:
            raise RuntimeError("Persistence/resume check failed.")
        print("Resumed persisted Alice conversation using a new SQLite connection.")
        turn(resumed, client, args.deployment, "alice", thread,
             "What is my notebook label?", args.budget, args.reserve)
        if resumed.load("bob", thread):
            raise RuntimeError("Cross-user history leak.")
        turn(resumed, client, args.deployment, "bob", thread,
             "What is my notebook label?", args.budget, args.reserve)
        if "MAPLE-742" in json.dumps(resumed.load("bob", thread)):
            raise RuntimeError("Bob received Alice's marker.")
        print("Bob used the same thread ID but a different user scope: no Alice history.")
        # Force enough bounded turns to evict the opening pair, without summarizing it.
        for number in range(5):
            turn(resumed, client, args.deployment, "alice", thread,
                 f"Lesson {number}: explain resource cleanup briefly. " + "Practice. " * 18,
                 args.budget, args.reserve)
        if "MAPLE-742" in json.dumps(resumed.load("alice", thread)):
            raise RuntimeError("Opening fact was not evicted; use the default 800 budget.")
        print("Opening notebook fact is absent from stored/model history after truncation.")
        turn(resumed, client, args.deployment, "alice", thread,
             "What is my notebook label? Do not guess.", args.budget, args.reserve)
        resumed.reset("alice", thread)
        if resumed.load("alice", thread):
            raise RuntimeError("Reset failed.")
        if not resumed.load("bob", thread):
            raise RuntimeError("Reset crossed user scope.")
        print("Reset Alice to empty history; Bob was unchanged.")
    finally:
        for user in ("alice", "bob"):
            store.delete(user, thread)
            if store.load(user, thread):
                raise RuntimeError("Deletion failed.")
        print("Deleted both demonstration conversations.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("demo", "turn", "show", "reset", "delete"), default="demo")
    parser.add_argument("--state", default=".state/conversations.sqlite3")
    parser.add_argument("--user", default="alice", help="Trusted app scope in this local teaching CLI.")
    parser.add_argument("--conversation", default="workshop")
    parser.add_argument("--prompt", default="Explain Microsoft Foundry in one sentence.")
    parser.add_argument("--budget", type=int, default=800, help="Conservative context token bound.")
    parser.add_argument("--reserve", type=int, default=120, help="Output token reserve.")
    parser.add_argument("--endpoint", default=os.getenv("AZURE_OPENAI_ENDPOINT"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"))
    args = parser.parse_args()
    if not 1 <= args.reserve < args.budget:
        parser.error("Require 1 <= reserve < budget.")
    store = Store(args.state)
    if args.action == "show":
        print(json.dumps(store.load(args.user, args.conversation), indent=2))
    elif args.action == "reset":
        store.reset(args.user, args.conversation)
        print("Reset scoped conversation.")
    elif args.action == "delete":
        print(f"Deleted {store.delete(args.user, args.conversation)} scoped conversation.")
    else:
        if not args.endpoint or not args.endpoint.startswith("https://"):
            parser.error("Set AZURE_OPENAI_ENDPOINT to the deployed HTTPS inference endpoint.")
        from azure.identity import AzureCliCredential, get_bearer_token_provider
        from openai import OpenAI

        with AzureCliCredential() as credential, OpenAI(
            base_url=args.endpoint.rstrip("/") + "/openai/v1/",
            api_key=get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default"),
        ) as client:
            if args.action == "demo":
                demonstration(store, client, args)
            else:
                turn(store, client, args.deployment, args.user, args.conversation,
                     args.prompt, args.budget, args.reserve)


if __name__ == "__main__":
    main()
