"""The agent as an HTTP service (FastAPI), always running like the MCP server.

Run:
    uv run uvicorn agent.api:app --port 8001          # on the Mac
    docker compose up -d                              # in Docker (port 8001)

    POST /chat {"message", "thread_id"} -> graph (llm <-> tools) -> {"thread_id", "steps", "answer", "result"}
    GET  /health

The graph is built once at startup, so a question only waits for Gemini + SQL.
thread_id = chat session: the same thread_id continues the conversation (the graph's checkpointer keeps the
messages per thread). No thread_id -> a new session. Sessions are saved in SQLite (chat_db in settings.yaml),
so they survive a restart.
"""

import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

from agent.graph import build_graph
from agent.setting_reader import load_settings


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None     # chat session; None -> a new one is made and returned


class Step(BaseModel):
    tool: str
    sql: str | None
    error: str | None = None         # set when the database rejected this SQL (the LLM then retries)


class ChatResponse(BaseModel):
    thread_id: str
    steps: list[Step]                # every tool call, in order
    answer: str | None               # the LLM's text (e.g. "no dataset fits"), when there are no rows
    result: dict[str, Any] | None    # {columns, rows, row_count, truncated}: the rows go straight to the user


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once: open the chat database, build the graph, serve, then close the database on shutdown."""
    chat_db = load_settings().chat_db
    chat_db.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(chat_db)) as checkpointer:  # tables are created on first use
        app.state.graph = await build_graph(checkpointer)  # fails (service stops) if the MCP server can't be used
        yield


app = FastAPI(title="query-agent", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/chat")
async def chat(req: ChatRequest) -> ChatResponse:
    thread_id = req.thread_id or uuid.uuid4().hex
    steps, answer, result = [], None, None
    try:
        inputs = {"messages": [("user", req.message)], "result": None}  # result: None clears the last turn's rows
        config = {"configurable": {"thread_id": thread_id}}            # which session to load and save
        async for update in app.state.graph.astream(inputs, config=config, stream_mode="updates"):
            for node, out in update.items():
                if node == "llm":
                    reply = out["messages"][-1]
                    steps += [Step(tool=c["name"], sql=c["args"].get("sql")) for c in reply.tool_calls]
                    answer = reply.text or None
                else:  # tools: one ToolMessage per call, in the same order as the calls
                    for step, msg in zip(steps[-len(out["messages"]):], out["messages"]):
                        if msg.status == "error":
                            step.error = msg.text
                    result = out["result"] or result
    except GraphRecursionError:
        answer = "Couldn't answer within the step limit (recursion_limit in settings.yaml)."
    except Exception as e:  # e.g. a Gemini rate-limit error; tell the client instead of a bare 500
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from None
    return ChatResponse(thread_id=thread_id, steps=steps, answer=answer, result=result)
