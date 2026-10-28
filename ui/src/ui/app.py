"""Chat UI (Streamlit) for the agent service. Run: uv run ui (the agent must be running: docker compose up -d).

    sidebar: "New chat" + the chat list (GET /threads)        main: the open chat (GET /threads/{id})
    first message in a fresh chat -> the UI makes a thread_id, then POST /chat {thread_id, message}

Streamlit re-runs this whole script on every click or message; st.session_state is what survives a re-run:
  thread_id  the open chat (None = fresh chat)
  results    the tables of this page session, per (thread_id, turn): the shown table (<= 30 rows) and the full
             results for Download. Rows are never saved anywhere, so a page refresh clears them; the answer
             text stays (it's in the chat history), and the table shows "ask again to see it".
"""

import os
import uuid
from pathlib import Path

import httpx
import pandas as pd
import streamlit as st
import yaml

SETTINGS = yaml.safe_load((Path(__file__).parent / "settings.yaml").read_text())
AGENT_URL = os.getenv("AGENT_URL") or SETTINGS["agent_url"]
TITLE = "Eddie's Query Agent"

# Sidebar: a chat's delete icon shows only while the mouse is over that chat's row (like ChatGPT).
# Streamlit has no hover option, so this CSS uses its class names: a container with key="row-x" gets the
# class "st-key-row-x", and a popover is data-testid="stPopover" (checked in Streamlit 1.66; may change).
HOVER_CSS = """<style>
[class*="st-key-row-"] [data-testid="stPopover"] { opacity: 0; transition: opacity 0.15s; }
[class*="st-key-row-"]:hover [data-testid="stPopover"],
[class*="st-key-row-"] [data-testid="stPopover"]:focus-within { opacity: 1; }
</style>"""


def agent(method: str, path: str, **kwargs) -> dict | list:
    """Call the agent service; raises RuntimeError with a readable message."""
    try:
        reply = httpx.request(method, AGENT_URL + path, timeout=120, **kwargs)
        reply.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Agent error {e.response.status_code}: {e.response.json().get('detail')}") from None
    except httpx.HTTPError as e:
        raise RuntimeError(f"Can't reach the agent at {AGENT_URL} ({e}). Is it running? docker compose up -d") from None
    return reply.json()


def open_chat(thread_id: str | None) -> None:
    st.session_state.thread_id = thread_id


def delete_chat(thread_id: str) -> None:
    try:
        agent("DELETE", f"/threads/{thread_id}")
    except RuntimeError as e:
        st.toast(str(e))
        return
    st.session_state.results = {k: v for k, v in st.session_state.results.items() if not k.startswith(f"{thread_id}:")}
    if st.session_state.thread_id == thread_id:  # deleted the open chat -> fresh chat
        st.session_state.thread_id = None


def sidebar() -> None:
    with st.sidebar:
        st.html(HOVER_CSS)
        st.button("New chat", icon=":material/edit_square:", width="stretch", on_click=open_chat, args=(None,))
        st.caption("Chats")
        try:
            threads = agent("GET", "/threads")
        except RuntimeError as e:
            st.error(str(e))
            return
        for t in threads:  # the sidebar scrolls when the list is long
            tid = t["thread_id"]
            row = st.container(key=f"row-{tid}")  # one row = chat button + delete icon (shown on hover)
            chat_col, delete_col = row.columns([5, 1], vertical_alignment="center")
            chat_col.button(tid[:8], key=f"open-{tid}", width="stretch",
                            type="primary" if tid == st.session_state.thread_id else "secondary",
                            on_click=open_chat, args=(tid,))
            with delete_col.popover("", icon=":material/delete:", type="tertiary", help="Delete chat"):
                st.caption("Delete this chat? This can't be undone.")
                st.button("Delete", key=f"delete-{tid}", type="primary", on_click=delete_chat, args=(tid,))


def download_button(result: dict, key: str) -> None:
    """Download CSV of a full result (from this page session's memory)."""
    csv = pd.DataFrame(result["rows"], columns=result["columns"]).to_csv(index=False)
    n = result["row_count"]
    st.download_button(f"Download CSV ({n:,} row{'' if n == 1 else 's'})", csv, file_name="result.csv", mime="text/csv",
                       icon=":material/download:", key=f"download-{key}")


def show_turn(turn: dict, cached: dict | None, key: str) -> None:
    """One answer: the text, then the table if the agent chose one, then what the agent did.
    cached = {"table", "results"} from this page session's /chat reply; None after a refresh or for old chats."""
    results = cached["results"] if cached else {}
    if turn["answer"]:
        st.markdown(turn["answer"].replace("$", r"\$"))  # "$" would start a math formula in Streamlit's Markdown
    if turn["table"]:  # the agent chose a table to show
        if cached and cached["table"]:
            table = cached["table"]
            shown = len(table["rows"])
            st.caption(f"showing {shown} of {table['row_count']:,} rows" if shown < table["row_count"] else f"{shown} rows")
            st.dataframe(pd.DataFrame(table["rows"], columns=table["columns"]), hide_index=True)
            download_button(results[table["result_id"]], key)
        else:
            st.caption("The table isn't saved: ask again to see it.")
    elif not turn["answer"] and turn["steps"]:  # old v1 chats: no saved answer
        st.caption("The result isn't saved: ask again to see it.")
    if turn["steps"]:
        with st.expander("What the agent did"):
            for i, step in enumerate(turn["steps"]):
                st.caption(step["tool"])
                st.code(step["sql"] or "", language="sql")
                if step["error"]:
                    st.error(step["error"])
                elif step["result_id"] in results and step["result_id"] != turn["table"]:  # other queries' results
                    download_button(results[step["result_id"]], f"{key}-step{i}")


def send(thread_id: str, message: str, turn_index: int) -> None:
    """POST /chat and keep this turn's table + full results for this page session."""
    with st.chat_message("user"):
        st.markdown(message)
    with st.chat_message("assistant"), st.spinner("Thinking..."):
        try:
            reply = agent("POST", "/chat", json={"message": message, "thread_id": thread_id})
        except RuntimeError as e:
            st.error(str(e))
            return
    st.session_state.results[f"{thread_id}:{turn_index}"] = {"table": reply["table"], "results": reply["results"]}
    st.rerun()  # draw the chat again from the agent's saved history (now with this turn)


def chat_page() -> None:
    st.session_state.setdefault("thread_id", None)
    st.session_state.setdefault("results", {})
    sidebar()

    thread_id = st.session_state.thread_id
    turns = []
    if thread_id:
        try:
            turns = agent("GET", f"/threads/{thread_id}")["turns"]
        except RuntimeError as e:
            st.error(str(e))

    if not turns:  # fresh chat: big title + the text box in the middle
        hero = st.empty()
        with hero.container():
            st.markdown(f"<h1 style='text-align:center; margin-top:25vh'>{TITLE}</h1>", unsafe_allow_html=True)
            message = st.chat_input("Ask a question about the data")
        if message:
            hero.empty()  # the title gives way to the chat
            new_id = thread_id or uuid.uuid4().hex  # a new chat gets its id first
            st.session_state.thread_id = new_id
            send(new_id, message, turn_index=0)
        return

    for i, turn in enumerate(turns):
        with st.chat_message("user"):
            st.markdown(turn["question"])
        with st.chat_message("assistant"):
            show_turn(turn, st.session_state.results.get(f"{thread_id}:{i}"), key=f"{thread_id}:{i}")
    if message := st.chat_input("Ask a follow-up"):
        send(thread_id, message, turn_index=len(turns))


def go_to_chat() -> None:
    st.switch_page(CHAT)  # "/" -> "/chat"


st.set_page_config(page_title=TITLE, page_icon=":material/database:", layout="centered")
CHAT = st.Page(chat_page, title=TITLE, url_path="chat")
st.navigation([st.Page(go_to_chat, default=True), CHAT], position="hidden").run()
