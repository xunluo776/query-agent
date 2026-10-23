"""The chat model + prompt chain that the graph's llm node calls.

    {"messages": [...]} -> [system prompt + messages] -> Gemini (MCP tools bound) -> AIMessage (text or tool calls)

The model is stateless: every call gets the system prompt and the whole message list again (Ch4).
"""

from pathlib import Path

from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_google_genai import ChatGoogleGenerativeAI

from agent.setting_reader import load_settings

SYSTEM_PROMPT = (Path(__file__).resolve().parent / "system_prompt.md").read_text()


def build_chain(tools: list[BaseTool]) -> Runnable:
    """Gemini with the MCP tools bound, behind the system prompt. Input: {"messages": [...]}."""
    model = ChatGoogleGenerativeAI(model=load_settings().model)  # load_settings also loads GOOGLE_API_KEY from .env
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(SYSTEM_PROMPT),  # a message object, not ("system", text): {braces} in the .md stay plain text
        MessagesPlaceholder("messages"),
    ])
    return prompt | model.bind_tools(tools)
