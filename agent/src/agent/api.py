"""The agent as an HTTP service (FastAPI), always running like the MCP server.

Run:
    uv run uvicorn agent.api:app --port 8001          # on the Mac
    docker compose up -d                              # in Docker (port 8001)

    POST /chat {"message", "thread_id"}  -> graph (llm <-> tools) -> {"thread_id", "steps", "answer", "result"}
    GET  /threads                        -> the chat list for the UI sidebar, most recent first
    GET  /threads/{thread_id}            -> one chat's history: {"thread_id", "turns"} (no rows)
    DELETE /threads/{thread_id}          -> delete the chat (its saved messages + its row in the list)
    GET  /health

Rows are never saved: only the /chat reply carries them (the UI keeps them for that page session).

The graph is built once at startup, so a question only waits for Gemini + SQL.
thread_id = chat session: the same thread_id continues the conversation (the graph's checkpointer keeps the
messages per thread). No thread_id -> a new session. Sessions are saved in SQLite (chat_db in settings.yaml),
so they survive a restart.
"""

import uuid
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite
from fastapi import FastAPI, HTTPException
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

from agent.graph import build_graph
from agent.setting_reader import load_settings
from agent.threads import ThreadStore


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None     # chat session; None -> a new one is made and returned


class Step(BaseModel):
    tool: str
    sql: str | None
    error: str | None = None         # set when the database rejected this SQL (the LLM then retries)
    result_id: str | None = None     # set when this query returned rows (r1, r2, ... per question)


class Turn(BaseModel):
    question: str
    steps: list[Step]                # every tool call, in order (the final `answer` call is not a step)
    answer: str | None               # the LLM's text
    table: str | None = None         # result_id the LLM chose to show as a table (None = no table)


class ChatResponse(BaseModel):
    thread_id: str
    steps: list[Step]
    answer: str | None
    table: dict[str, Any] | None     # the chosen result, first read_cap rows: {result_id, columns, rows, row_count, truncated}
    results: dict[str, dict]         # every full result of this question by result_id (for Download); never saved


class ThreadHistory(BaseModel):
    thread_id: str
    turns: list[Turn]                # questions, SQL and answers only; rows aren't saved


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once: open the chat database, build the graph, serve, then close the database on shutdown."""
    chat_db = load_settings().chat_db
    chat_db.parent.mkdir(parents=True, exist_ok=True)
    async with (
        AsyncSqliteSaver.from_conn_string(str(chat_db)) as checkpointer,  # messages per chat (tables made on first use)
        aiosqlite.connect(chat_db) as conn,                               # our own chat list, same file
    ):
        app.state.checkpointer = checkpointer
        app.state.threads = ThreadStore(conn)
        await app.state.threads.setup()
        app.state.graph = await build_graph(checkpointer)  # fails (service stops) if the MCP server can't be used
        yield


app = FastAPI(title="query-agent", lifespan=lifespan)


def to_turns(messages: list[AnyMessage]) -> list[Turn]:
    """Saved messages -> one Turn per user question (the UI shows these)."""
    turns, steps_by_call = [], {}
    for m in messages:
        if isinstance(m, HumanMessage):
            turns.append(Turn(question=m.text, steps=[], answer=None))
        elif isinstance(m, AIMessage) and turns:
            for call in m.tool_calls:
                if call["name"] == "answer":  # the final answer, not a step
                    turns[-1].answer = call["args"].get("text")
                    turns[-1].table = call["args"].get("table")
                    continue
                step = Step(tool=call["name"], sql=call["args"].get("sql"))
                turns[-1].steps.append(step)
                steps_by_call[call["id"]] = step
            turns[-1].answer = m.text or turns[-1].answer  # fallback: a plain-text reply
        elif isinstance(m, ToolMessage) and m.tool_call_id in steps_by_call:
            step = steps_by_call[m.tool_call_id]
            if m.status == "error":
                step.error = m.text
            elif isinstance(m.artifact, dict):
                step.result_id = m.artifact.get("result_id")
    return turns


def table_preview(result: dict, result_id: str) -> dict:
    """The table shown under the answer: the first read_cap rows (the full result is for Download)."""
    rows = result["rows"][: load_settings().read_cap]
    return {"result_id": result_id, "columns": result["columns"], "rows": rows,
            "row_count": result["row_count"], "truncated": result["truncated"]}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/chat")
async def chat(req: ChatRequest) -> ChatResponse:
    thread_id = req.thread_id or uuid.uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}            # which session to load and save
    inputs = {"messages": [("user", req.message)], "result_count": 0}  # result ids restart at r1 per question
    await app.state.threads.touch(thread_id)                       # listed in the sidebar even if the run fails
    answer, results = None, {}
    try:
        async for chunk in app.state.graph.astream(inputs, config=config, stream_mode="custom"):
            results[chunk["result_id"]] = chunk["result"]  # full rows from the tools node (not saved)
    except GraphRecursionError:
        answer = "Couldn't answer within the step limit (recursion_limit in settings.yaml)."
    except Exception as e:  # e.g. a Gemini rate-limit error; tell the client instead of a bare 500
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from None

    state = (await app.state.graph.aget_state(config)).values      # what the checkpointer saved for this chat
    turn = to_turns(state["messages"])[-1]
    table = table_preview(results[turn.table], turn.table) if turn.table in results else None  # ignores a wrong id
    return ChatResponse(thread_id=thread_id, steps=turn.steps, answer=answer or turn.answer, table=table, results=results)


@app.get("/threads")
async def list_threads() -> list[dict]:
    return await app.state.threads.list()


@app.get("/threads/{thread_id}")
async def get_thread(thread_id: str) -> ThreadHistory:
    state = (await app.state.graph.aget_state({"configurable": {"thread_id": thread_id}})).values
    if not state.get("messages"):
        raise HTTPException(status_code=404, detail=f"no chat with thread_id {thread_id!r}")
    return ThreadHistory(thread_id=thread_id, turns=to_turns(state["messages"]))


@app.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str) -> dict:
    """Delete a chat in both places: the checkpointer's saved messages and our chat list."""
    await app.state.checkpointer.adelete_thread(thread_id)
    if not await app.state.threads.delete(thread_id):
        raise HTTPException(status_code=404, detail=f"no chat with thread_id {thread_id!r}")
    return {"deleted": thread_id}
