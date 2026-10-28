"""The agent loop as a LangGraph graph (Ch4 tool loop, as explicit nodes and edges; Ch10 §10.3).

    START -> llm --no tool call--> END             (the answer: analysis of the results, or "no dataset fits")
              |
          tool call
              v
            tools ---------------> llm             (every tool result goes back: boundary, SQL error, query result)

v2: the LLM reads each query result to explain it, but only a capped view (read_cap rows + how much it isn't
seeing); that view is saved in the chat history. The full result goes to the user on the custom stream
(get_stream_writer(); the caller reads stream_mode="custom") and is never saved.
recursion_limit (settings.yaml) stops the loop; LangGraph then raises GraphRecursionError.

Chat sessions: the checkpointer saves the state per thread_id (passed in the run config), so the next
message in the same thread sees the earlier conversation. api.py passes a SQLite checkpointer.
"""

import json
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from agent.llm import build_chain
from agent.mcp_connection import get_tools
from agent.setting_reader import load_settings


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]  # add_messages: nodes append, not replace


def result_view(result: dict, read_cap: int) -> str:
    """What the LLM reads from a query result: at most read_cap rows, and a note on how much it isn't seeing."""
    rows, n = result["rows"][:read_cap], result["row_count"]
    if result["truncated"]:
        note = f"showing {len(rows)} of more than {n:,} rows (the result was cut at the server's row limit)"
    elif n > read_cap:
        note = f"showing {len(rows)} of {n:,} rows"
    else:
        note = f"showing all {n} rows"
    view = {"columns": result["columns"], "row_count": n, "truncated": result["truncated"], "rows": rows, "note": note}
    return json.dumps(view, default=str)


async def build_graph(checkpointer: BaseCheckpointSaver):
    """Connect to the MCP server, build the chain, and wire the graph. Input: {"messages": [...]}.
    checkpointer: where chat sessions are saved (SQLite now, Postgres later: same interface)."""
    settings = load_settings()
    tools = await get_tools()
    tools_by_name = {t.name: t for t in tools}
    chain = build_chain(tools)

    async def llm(state: State) -> dict:
        reply = await chain.ainvoke({"messages": state["messages"]})
        return {"messages": [reply]}

    async def run_tools(state: State) -> dict:
        """Run every tool call in the LLM's reply. Every call needs a ToolMessage back (the model expects one)."""
        send = get_stream_writer()  # to the caller (stream_mode="custom"); not saved by the checkpointer
        messages = []
        for call in state["messages"][-1].tool_calls:
            tool = tools_by_name.get(call["name"])
            if tool is None:
                messages.append(ToolMessage(f"Unknown tool {call['name']!r}.", tool_call_id=call["id"], status="error"))
                continue
            msg = await tool.ainvoke(call)  # ToolMessage; status "error" if the server raised ToolError
            if msg.status != "error" and call["name"].endswith("_query"):  # the query tool, not the boundary
                result = json.loads(msg.text)  # {columns, rows, row_count, truncated}
                send({"tool_call_id": call["id"], "result": result})  # full result -> the user
                msg = ToolMessage(result_view(result, settings.read_cap), tool_call_id=call["id"])  # capped -> the LLM
            messages.append(msg)
        return {"messages": messages}

    def after_llm(state: State) -> str:
        return "tools" if state["messages"][-1].tool_calls else END

    builder = StateGraph(State)
    builder.add_node("llm", llm)
    builder.add_node("tools", run_tools)
    builder.add_edge(START, "llm")
    builder.add_conditional_edges("llm", after_llm, ["tools", END])
    builder.add_edge("tools", "llm")  # the LLM always sees what the tools returned
    graph = builder.compile(checkpointer=checkpointer)  # memory per thread_id
    return graph.with_config(recursion_limit=settings.recursion_limit)
