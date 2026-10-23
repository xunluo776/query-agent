"""Terminal chat client for the agent service: uv run agent (the service must be running: docker compose up -d).

    question -> POST {api_url}/chat -> the SQL that ran + the first rows

Only an HTTP client: the agent itself (graph, LLM, MCP tools) runs in the service (api.py).
"""

import uuid

import httpx

from agent.setting_reader import load_settings

SHOW_ROWS = 20   # the full result goes to the UI later (Phase 4)
MAX_WIDTH = 40   # cut long values so the table fits the terminal


def show(reply: dict) -> None:
    for step in reply["steps"]:
        print(f"  -> {step['tool']}: {step['sql']}")
        if step["error"]:
            print(f"  <- error: {step['error']}")
    if reply["result"] is None:
        print(reply["answer"] or "No answer.")
    else:
        print_result(reply["result"])


def print_result(result: dict) -> None:
    rows = result["rows"]
    print(f"{result['row_count']} rows" + (" (truncated at the server's row limit)" if result["truncated"] else ""))
    table = [result["columns"]] + [[str(v)[:MAX_WIDTH] for v in row] for row in rows[:SHOW_ROWS]]
    widths = [max(len(str(row[i])) for row in table) for i in range(len(result["columns"]))]
    for row in table:
        print("  " + " | ".join(str(v).ljust(w) for v, w in zip(row, widths)))
    if len(rows) > SHOW_ROWS:
        print(f"  ... {len(rows) - SHOW_ROWS} more rows")


def main() -> None:
    """Entry point for `uv run agent`."""
    api_url = load_settings().api_url
    thread_id = uuid.uuid4().hex  # one chat session per CLI run: follow-up questions see the earlier ones
    with httpx.Client(base_url=api_url, timeout=120) as client:
        try:
            client.get("/health").raise_for_status()
        except httpx.HTTPError as e:
            print(f"Can't reach the agent service at {api_url} ({e}). Is it running? docker compose up -d")
            return

        print("Ask a question about the data (empty line or Ctrl-D to quit).")
        while True:
            try:
                question = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not question:
                break
            try:
                reply = client.post("/chat", json={"message": question, "thread_id": thread_id})
                reply.raise_for_status()
            except httpx.HTTPStatusError as e:  # the service answered with an error (e.g. 502 Gemini rate limit)
                print(f"Error {e.response.status_code}: {e.response.json().get('detail')}")
                continue
            except httpx.HTTPError as e:        # no answer at all (service down, timeout)
                print(f"Error: {e}")
                continue
            show(reply.json())
