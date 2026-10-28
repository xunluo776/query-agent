"""The agent as an HTTP service (FastAPI), always running like the MCP server.

Run:
    uv run uvicorn agent.api:app --port 8001          # on the Mac
    docker compose up -d                              # in Docker (port 8001)

    POST /chat {"message", "thread_id"}  -> graph (llm <-> tools) -> {"thread_id", "steps", "answer", "table", "results"}
    GET  /threads                        -> the chat list for the UI sidebar, most recent first
    GET  /threads/{thread_id}            -> one chat's history: {"thread_id", "turns"} (no rows)
    DELETE /threads/{thread_id}          -> delete the chat (its saved messages, its row in the list, its result files)
    GET  /results/{thread_id}/{result_id} -> a full result as CSV (Download); 404 once it has expired
    GET  /health

Rows: the /chat reply carries only the table preview (<= read_cap rows). Full results are Parquet files
(result_store.py), deleted after result_ttl_min (a background task checks every few minutes).

The graph is built once at startup, so a question only waits for Gemini + SQL.
thread_id = chat session: the same thread_id continues the conversation (the graph's checkpointer keeps the
messages per thread). No thread_id -> a new session. Sessions are saved in SQLite (chat_db in settings.yaml),
so they survive a restart.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager, suppress
from typing import Any

import aiosqlite
from fastapi import FastAPI, HTTPException, Response
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

from agent.graph import build_graph
from agent.result_store import ResultStore
from agent.setting_reader import load_settings
from agent.threads import ThreadStore


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None     # chat session; None -> a new one is made and returned


class Step(BaseModel):
    tool: str
    sql: str | None
    error: str | None = None         # set when the database rejected this SQL (the LLM then retries)
    result_id: str | None = None     # set when this query returned rows (r1, r2, ... per chat)


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
    results: dict[str, dict]         # every result of this question by result_id: {row_count} (Download: GET /results/...)


class ThreadHistory(BaseModel):
    thread_id: str
    turns: list[Turn]                # questions, SQL and answers only; rows aren't saved


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once: open the chat database and the result store, build the graph, serve; close on shutdown."""
    settings = load_settings()
    chat_db = settings.chat_db
    chat_db.parent.mkdir(parents=True, exist_ok=True)
    app.state.store = ResultStore(settings.results_dir, settings.result_ttl_min)
    cleanup = asyncio.create_task(cleanup_results(app.state.store))  # like an S3 lifecycle rule
    async with (
        AsyncSqliteSaver.from_conn_string(str(chat_db)) as checkpointer,  # messages per chat (tables made on first use)
        aiosqlite.connect(chat_db) as conn,                               # our own chat list, same file
    ):
        app.state.checkpointer = checkpointer
        app.state.threads = ThreadStore(conn)
        await app.state.threads.setup()
        app.state.graph = await build_graph(checkpointer, app.state.store)  # fails if the MCP server can't be used
        yield
    cleanup.cancel()
    with suppress(asyncio.CancelledError):
        await cleanup


async def cleanup_results(store: ResultStore, every_sec: int = 300) -> None:
    """Delete expired result files at startup and then every few minutes."""
    while True:
        await asyncio.to_thread(store.cleanup)
        await asyncio.sleep(every_sec)


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


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/chat")
async def chat(req: ChatRequest) -> ChatResponse:
    thread_id = req.thread_id or uuid.uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}            # which session to load and save
    inputs = {"messages": [("user", req.message)]}
    await app.state.threads.touch(thread_id)                       # listed in the sidebar even if the run fails
    answer, previews = None, {}
    try:
        async for chunk in app.state.graph.astream(inputs, config=config, stream_mode="custom"):
            previews[chunk["result_id"]] = chunk  # <= read_cap rows; the full result is in a file
    except GraphRecursionError:
        answer = "Couldn't answer within the step limit (recursion_limit in settings.yaml)."
    except Exception as e:  # e.g. a Gemini rate-limit error; tell the client instead of a bare 500
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from None

    state = (await app.state.graph.aget_state(config)).values      # what the checkpointer saved for this chat
    turn = to_turns(state["messages"])[-1]
    table = previews.get(turn.table)  # None if no table was chosen (or a wrong id)
    results = {rid: {"row_count": p["row_count"]} for rid, p in previews.items()}
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
    """Delete a chat everywhere: the checkpointer's saved messages, our chat list and its result files."""
    await app.state.checkpointer.adelete_thread(thread_id)
    app.state.store.delete_thread(thread_id)
    if not await app.state.threads.delete(thread_id):
        raise HTTPException(status_code=404, detail=f"no chat with thread_id {thread_id!r}")
    return {"deleted": thread_id}


@app.get("/results/{thread_id}/{result_id}")
async def download_result(thread_id: str, result_id: str) -> Response:
    """A full result as CSV, like a presigned S3 download; 404 once the file has expired."""
    csv = await asyncio.to_thread(app.state.store.csv, thread_id, result_id)
    if csv is None:
        ttl_min = app.state.store.ttl_sec // 60
        raise HTTPException(status_code=404, detail=f"This result expired (kept {ttl_min} min). Ask again to get it.")
    return Response(csv, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{result_id}.csv"'})
