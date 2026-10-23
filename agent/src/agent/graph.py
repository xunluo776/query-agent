"""The agent loop as a LangGraph graph (Ch4 tool loop, as explicit nodes and edges; Ch10 §10.3).

    START -> llm --no tool call--> END             (text answer, e.g. "no dataset fits")
              |
          tool call
              v
            tools --query OK--> END                (rows sent out on the custom stream, pass-through)
              |
      boundary result / SQL error
              v
             llm                                   (sees the boundary or the error, writes the next SQL)

The LLM never sees the query rows (v1), and they are never saved: the tools node sends them out with
get_stream_writer() (the caller reads stream_mode="custom"), and messages only get a short note.
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
    query_ok: bool                                       # a query returned rows -> stop (the rows aren't in the state)


async def build_graph(checkpointer: BaseCheckpointSaver):
    """Connect to the MCP server, build the chain, and wire the graph. Input: {"messages": [...]}.
    checkpointer: where chat sessions are saved (SQLite now, Postgres later: same interface)."""
    tools = await get_tools()
    tools_by_name = {t.name: t for t in tools}
    chain = build_chain(tools)

    async def llm(state: State) -> dict:
        reply = await chain.ainvoke({"messages": state["messages"]})
        return {"messages": [reply]}

    async def run_tools(state: State) -> dict:
        """Run every tool call in the LLM's reply. Every call needs a ToolMessage back (the model expects one)."""
        send = get_stream_writer()  # to the caller (stream_mode="custom"); not saved by the checkpointer
        messages, query_ok = [], False
        for call in state["messages"][-1].tool_calls:
            tool = tools_by_name.get(call["name"])
            if tool is None:
                messages.append(ToolMessage(f"Unknown tool {call['name']!r}.", tool_call_id=call["id"], status="error"))
                continue
            msg = await tool.ainvoke(call)  # ToolMessage; status "error" if the server raised ToolError
            if msg.status != "error" and call["name"].endswith("_query"):  # the query tool, not the boundary
                result = json.loads(msg.text)  # {columns, rows, row_count, truncated}
                send({"result": result})
                query_ok = True
                msg = ToolMessage(f"Query OK: {result['row_count']} rows sent to the user.", tool_call_id=call["id"])
            messages.append(msg)
        return {"messages": messages, "query_ok": query_ok}

    def after_llm(state: State) -> str:
        return "tools" if state["messages"][-1].tool_calls else END

    def after_tools(state: State) -> str:
        return END if state["query_ok"] else "llm"  # boundary or error -> llm tries again

    builder = StateGraph(State)
    builder.add_node("llm", llm)
    builder.add_node("tools", run_tools)
    builder.add_edge(START, "llm")
    builder.add_conditional_edges("llm", after_llm, ["tools", END])
    builder.add_conditional_edges("tools", after_tools, ["llm", END])
    graph = builder.compile(checkpointer=checkpointer)  # memory per thread_id
    return graph.with_config(recursion_limit=load_settings().recursion_limit)
