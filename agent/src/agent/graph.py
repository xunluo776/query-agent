"""The agent loop as a LangGraph graph (Ch4 tool loop, as explicit nodes and edges; Ch10 §10.3).

    START -> llm --answer(text, table)--> finish -> END    (the final answer; finish adds no LLM call)
              |  `--plain text---------------------> END   (fallback: text only, no table)
          tool call
              v
            tools ---------------> llm                    (every tool result goes back: boundary, SQL error, query result)

v2: the LLM reads each query result to explain it, but only a capped view (read_cap rows + how much it isn't
seeing), tagged with a result_id (r1, r2, ... per chat); that view is saved in the chat history. The full result
is saved as a Parquet file (result_store.py, deleted after a TTL), and a preview (read_cap rows) goes to the caller
on the custom stream (get_stream_writer(); stream_mode="custom"). The LLM ends by calling `answer` with its text
and the result_id of the table to show (or none).
recursion_limit (settings.yaml) stops the loop; LangGraph then raises GraphRecursionError.

Chat sessions: the checkpointer saves the state per thread_id (passed in the run config), so the next
message in the same thread sees the earlier conversation. api.py passes a SQLite checkpointer.
"""

import json
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from agent.llm import build_chain
from agent.mcp_connection import get_tools
from agent.result_store import ResultStore
from agent.setting_reader import load_settings


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]  # add_messages: nodes append, not replace
    result_count: int                                    # query results so far in this chat (-> r1, r2, ...)


@tool(parse_docstring=True)
def answer(text: str, table: str | None = None) -> str:
    """Give your final answer to the user. Call this last and alone, once you have the results you need.

    Args:
        text: The answer in plain English, 2-4 sentences, with numbers from the results.
        table: The result_id (e.g. "r1") of the result to show the user as a table, or null when a table
            doesn't help (the answer is a single number or name).
    """
    return "Shown to the user."  # never run: the graph ends on this call (finish node)


def result_view(result: dict, result_id: str, read_cap: int) -> str:
    """What the LLM reads from a query result: at most read_cap rows, and a note on how much it isn't seeing."""
    rows, n = result["rows"][:read_cap], result["row_count"]
    if result["truncated"]:
        note = f"showing {len(rows)} of more than {n:,} rows (the result was cut at the server's row limit)"
    elif n > read_cap:
        note = f"showing {len(rows)} of {n:,} rows"
    else:
        note = f"showing all {n} rows"
    view = {"result_id": result_id, "columns": result["columns"], "row_count": n,
            "truncated": result["truncated"], "rows": rows, "note": note}
    return json.dumps(view, default=str)


async def build_graph(checkpointer: BaseCheckpointSaver, store: ResultStore):
    """Connect to the MCP server, build the chain, and wire the graph. Input: {"messages": [...]}.
    checkpointer: where chat sessions are saved (SQLite now, Postgres later: same interface).
    store: where full query results are saved as files."""
    settings = load_settings()
    tools = await get_tools()
    tools_by_name = {t.name: t for t in tools}
    chain = build_chain([*tools, answer])

    async def llm(state: State) -> dict:
        reply = await chain.ainvoke({"messages": state["messages"]})
        return {"messages": [reply]}

    async def run_tools(state: State, config: RunnableConfig) -> dict:
        """Run every tool call in the LLM's reply. Every call needs a ToolMessage back (the model expects one)."""
        send = get_stream_writer()  # to the caller (stream_mode="custom"); not saved by the checkpointer
        thread_id = config["configurable"]["thread_id"]
        messages, count = [], state.get("result_count", 0)  # ids keep counting across the chat's questions
        for call in state["messages"][-1].tool_calls:
            tool_ = tools_by_name.get(call["name"])
            if call["name"] == "answer":  # mixed with other calls: the results it would describe aren't back yet
                messages.append(ToolMessage("Call answer alone, after you have seen the results.",
                                            tool_call_id=call["id"], status="error"))
                continue
            if tool_ is None:
                messages.append(ToolMessage(f"Unknown tool {call['name']!r}.", tool_call_id=call["id"], status="error"))
                continue
            msg = await tool_.ainvoke(call)  # ToolMessage; status "error" if the server raised ToolError
            if msg.status != "error" and call["name"].endswith("_query"):  # the query tool, not the boundary
                count += 1
                result_id = f"r{count}"
                result = json.loads(msg.text)  # {columns, rows, row_count, truncated}
                store.save(thread_id, result_id, result)  # full result -> a file (Download, follow-ups)
                send({"result_id": result_id, "columns": result["columns"], "rows": result["rows"][: settings.read_cap],
                      "row_count": result["row_count"], "truncated": result["truncated"]})  # preview -> the user
                msg = ToolMessage(result_view(result, result_id, settings.read_cap),  # capped view -> the LLM
                                  tool_call_id=call["id"], artifact={"result_id": result_id})
            messages.append(msg)
        return {"messages": messages, "result_count": count}

    async def finish(state: State) -> dict:
        """Close the answer call (the history needs a reply to every tool call); no LLM call."""
        call = state["messages"][-1].tool_calls[0]
        return {"messages": [ToolMessage("Shown to the user.", tool_call_id=call["id"])]}

    def after_llm(state: State) -> str:
        calls = state["messages"][-1].tool_calls
        if not calls:
            return END  # plain text reply
        if [c["name"] for c in calls] == ["answer"]:
            return "finish"
        return "tools"

    builder = StateGraph(State)
    builder.add_node("llm", llm)
    builder.add_node("tools", run_tools)
    builder.add_node("finish", finish)
    builder.add_edge(START, "llm")
    builder.add_conditional_edges("llm", after_llm, ["tools", "finish", END])
    builder.add_edge("tools", "llm")  # the LLM always sees what the tools returned
    builder.add_edge("finish", END)
    graph = builder.compile(checkpointer=checkpointer)  # memory per thread_id
    return graph.with_config(recursion_limit=settings.recursion_limit)
