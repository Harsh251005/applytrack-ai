import html
import time
import queue
import asyncio
from concurrent.futures import ThreadPoolExecutor

import streamlit as st
from src.agent.tracker import stream_tracker_agent  # the async generator you added

# ==================================================================
# 1. Page Config & Theme
# ==================================================================
st.set_page_config(
    page_title="ApplyTrack AI",
    page_icon="🎯",
    layout="centered",
    initial_sidebar_state="collapsed"
)

st.html("""
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&family=Fira+Code:wght@400;500&display=swap" rel="stylesheet">
<style>
    :root {
        --bg-color: #0d0f19;
        --brand-blue: #0072ff;
        --brand-cyan: #00c6ff;
        --brand-pink: #f857a6;
        --glass-bg: rgba(255, 255, 255, 0.05);
        --glass-border: rgba(255, 255, 255, 0.1);
        --text-main: #e2e8f0;
        --text-muted: #94a3b8;
    }
    .stApp {
        background-color: var(--bg-color);
        background-image:
            radial-gradient(circle at 10% 20%, rgba(0, 198, 255, 0.1) 0%, transparent 40%),
            radial-gradient(circle at 90% 80%, rgba(248, 87, 166, 0.1) 0%, transparent 40%);
        background-attachment: fixed;
        font-family: 'Outfit', sans-serif !important;
        color: var(--text-main);
    }
    [data-testid="collapsedControl"], #MainMenu, footer, header { display: none !important; }
    .block-container { padding-top: 3rem !important; padding-bottom: 8rem !important; max-width: 800px; }

    .header-container { display: flex; flex-direction: column; align-items: center; justify-content: center; padding-bottom: 2.5rem; animation: fadeIn 1s ease-out; text-align: center; }
    .logo-wrapper { display: flex; align-items: center; justify-content: center; gap: 12px; }
    .target-icon { font-size: 2.8rem; animation: pulseLogo 2.5s infinite; filter: drop-shadow(0 0 10px rgba(0, 198, 255, 0.6)); }
    .brand-title { font-size: 2.5rem; font-weight: 700; margin: 0; background: linear-gradient(to right, var(--brand-cyan), var(--brand-blue), var(--brand-pink), var(--brand-cyan)); background-size: 300% auto; -webkit-background-clip: text; -webkit-text-fill-color: transparent; animation: textShine 5s linear infinite; }
    .brand-subtitle { color: var(--text-muted); font-size: 1rem; margin-top: 5px; font-weight: 400; }

    div[class*="st-key-bubble-"], div[class*="st-key-terminal-wrapper"] { width: 100%; max-width: 85%; clear: both; display: flex; flex-direction: column; }
    div[class*="st-key-bubble-user-"] > div, div[class*="st-key-bubble-assistant-"] > div { display: flex; align-items: center; min-height: 48px; }
    div[class*="st-key-bubble-user-"] { margin-left: auto !important; margin-right: 0 !important; align-items: flex-end; }
    div[class*="st-key-bubble-user-"] > div { background: linear-gradient(135deg, var(--brand-cyan), var(--brand-blue)); border-radius: 20px 20px 4px 20px; padding: 10px 20px 20px 20px; margin-bottom: 15px; color: white !important; box-shadow: 0 8px 20px rgba(0, 114, 255, 0.25); animation: slideInRight 0.4s cubic-bezier(0.25, 0.8, 0.2, 1) forwards; transform-origin: bottom right; text-align: left; width: fit-content; }
    div[class*="st-key-bubble-user-"] p { margin: 0; color: white !important; font-size: 1.05rem;}
    div[class*="st-key-bubble-assistant-"], div[class*="st-key-terminal-wrapper"] { margin-right: auto !important; margin-left: 0 !important; align-items: flex-start; }
    div[class*="st-key-bubble-assistant-"] > div { background: var(--glass-bg); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); border: 1px solid var(--glass-border); border-radius: 20px 20px 20px 4px; padding: 10px 20px 20px 20px; margin-bottom: 15px; color: var(--text-main) !important; box-shadow: 0 8px 20px rgba(0, 0, 0, 0.2); animation: slideInLeft 0.4s cubic-bezier(0.25, 0.8, 0.2, 1) forwards; transform-origin: bottom left; text-align: left; width: fit-content; }
    div[class*="st-key-bubble-assistant-"] p { margin: 0; font-size: 1.05rem; color: var(--text-main) !important;}

    [data-testid="stChatInput"] { background: transparent !important; padding-bottom: 1rem; }
    [data-testid="stChatInput"] textarea { border-radius: 24px !important; border: 1px solid var(--glass-border) !important; background: rgba(20, 24, 39, 0.8) !important; backdrop-filter: blur(10px); color: white !important; padding: 1rem 1.2rem !important; font-size: 1rem !important; box-shadow: 0 4px 20px rgba(0,0,0,0.3) !important; transition: all 0.3s ease; }
    [data-testid="stChatInput"] textarea:focus { border-color: var(--brand-cyan) !important; box-shadow: 0 0 0 2px rgba(0, 198, 255, 0.2) !important; }
    [data-testid="stChatInput"] button { background: var(--brand-blue) !important; border-radius: 50% !important; transition: transform 0.2s ease !important; }
    [data-testid="stChatInput"] button:hover { transform: scale(1.1); background: var(--brand-cyan) !important; }

    div[class*="st-key-clear-btn-container"] { position: fixed; top: 20px; right: 20px; width: auto; z-index: 1000; animation: fadeIn 1s ease-out; }
    div[class*="st-key-clear-btn-container"] button { border-radius: 30px !important; border: 1px solid rgba(255, 255, 255, 0.1) !important; background: rgba(10, 13, 22, 0.5) !important; backdrop-filter: blur(8px); color: var(--text-muted) !important; font-family: 'Outfit', sans-serif; font-size: 0.85rem !important; padding: 0.3rem 0.8rem !important; box-shadow: 0 4px 10px rgba(0, 0, 0, 0.3) !important; transition: all 0.3s ease !important; }
    div[class*="st-key-clear-btn-container"] button:hover { border-color: var(--brand-pink) !important; color: white !important; box-shadow: 0 0 15px rgba(248, 87, 166, 0.4) !important; transform: translateY(-2px); }

    /* ---------- Live Thinking Feed (replaces the old raw terminal) ---------- */
    .thinking-box {
        background: rgba(10, 13, 22, 0.85);
        backdrop-filter: blur(10px);
        border: 1px solid var(--glass-border);
        border-radius: 16px;
        margin-bottom: 15px;
        width: 100%;
        padding: 14px 18px;
        box-shadow: 0 10px 30px rgba(0, 198, 255, 0.08);
        animation: fadeIn 0.4s ease-out;
        text-align: left;
    }
    .think-line {
        line-height: 1.5;
        margin-bottom: 6px;
        font-size: 0.95rem;
        color: var(--text-main);
        opacity: 0.9;
    }
    .think-line.tool { color: var(--brand-cyan); font-size: 0.88rem; opacity: 0.85; }
    .think-line.result { color: var(--text-muted); font-size: 0.82rem; font-family: 'Fira Code', monospace; }
    .think-cursor { display: inline-block; width: 7px; height: 1em; background: var(--brand-pink); vertical-align: middle; animation: blink 1s step-end infinite; }

    @keyframes pulseLogo { 0%, 100% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.05); opacity: 0.8; } }
    @keyframes textShine { 0% { background-position: 0% center; } 100% { background-position: -300% center; } }
    @keyframes slideInRight { 0% { opacity: 0; transform: translateX(30px) scale(0.95); } 100% { opacity: 1; transform: translateX(0) scale(1); } }
    @keyframes slideInLeft { 0% { opacity: 0; transform: translateX(-30px) scale(0.95); } 100% { opacity: 1; transform: translateX(0) scale(1); } }
    @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
    @keyframes blink { 50% { opacity: 0; } }
</style>
""")

# ==================================================================
# 2. Background agent runner — drains the async event stream into a queue
# ==================================================================
def run_agent_with_live_log(prompt: str, log_queue: queue.Queue) -> str:
    final_text = ""

    async def _drain():
        nonlocal final_text
        async for item in stream_tracker_agent(prompt):
            if item["kind"] == "final":
                final_text = item["text"]
            else:
                log_queue.put(item)

    try:
        asyncio.run(_drain())
    except Exception as e:
        final_text = f"Agent Error: {str(e)}"

    return final_text


# ==================================================================
# 3. Header & Floating Utilities
# ==================================================================
st.html("""
<div class="header-container">
    <div class="logo-wrapper">
        <span class="target-icon">🎯</span>
        <h1 class="brand-title">ApplyTrack AI</h1>
    </div>
    <p class="brand-subtitle">Automating your job hunt sheet, intelligently.</p>
</div>
""")

with st.container(key="clear-btn-container"):
    if st.button("🗑️ Clear Chat", use_container_width=False):
        st.session_state.messages = []
        st.rerun()

# ==================================================================
# 4. Session State & Chat Rendering
# ==================================================================
if "messages" not in st.session_state:
    st.session_state.messages = []


def render_bubble(role: str, content: str, key: str):
    with st.container(key=key):
        st.markdown(content)


for i, msg in enumerate(st.session_state.messages):
    render_bubble(msg["role"], msg["content"], key=f"bubble-{msg['role']}-{i}")


# ==================================================================
# 5. Live Thinking Feed & Chat Input
# ==================================================================
def render_thinking(placeholder, lines: list):
    """lines: list of ('thought' | 'tool' | 'result', text)"""
    rendered = []
    for kind, text in lines:
        css_class = {"thought": "think-line", "tool": "think-line tool", "result": "think-line result"}[kind]
        prefix = {"thought": "", "tool": "🔧 ", "result": "→ "}[kind]
        rendered.append(f'<div class="{css_class}">{prefix}{html.escape(text)}</div>')

    html_content = f"""
    <div class="thinking-box">
        {''.join(rendered)}
        <div class="think-line"><span class="think-cursor"></span></div>
    </div>
    """
    placeholder.html(html_content)


def stream_final_response(text: str):
    for word in text.split(" "):
        yield word + " "
        time.sleep(0.03)


prompt = st.chat_input("Tell ApplyTrack what you applied for today...")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    render_bubble("user", prompt, key=f"bubble-user-{len(st.session_state.messages)}")

    with st.container(key="terminal-wrapper"):
        thinking_placeholder = st.empty()

        log_queue = queue.Queue()
        # (kind, text) pairs to render; a running "thought" line gets appended to
        # in place as deltas arrive, instead of adding a new line per token.
        lines = []
        current_thought = ""

        render_thinking(thinking_placeholder, lines)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_agent_with_live_log, prompt, log_queue)

            while not future.done():
                try:
                    item = log_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                kind = item["kind"]

                if kind == "thought_delta":
                    current_thought += item["text"]
                    # live-update the last thought line rather than spamming new ones
                    if lines and lines[-1][0] == "thought" and lines[-1][2] == "open":
                        lines[-1] = ("thought", current_thought, "open")
                    else:
                        lines.append(("thought", current_thought, "open"))

                elif kind == "thought_done":
                    if lines and lines[-1][0] == "thought":
                        lines[-1] = ("thought", lines[-1][1], "closed")
                    current_thought = ""

                elif kind == "tool_call":
                    lines.append(("tool", item["text"], "closed"))

                elif kind == "tool_result":
                    lines.append(("result", item["text"], "closed"))

                render_thinking(thinking_placeholder, [(k, t) for k, t, _ in lines])

            final_response = future.result()

        thinking_placeholder.empty()

    with st.container(key=f"bubble-assistant-{len(st.session_state.messages)}"):
        st.write_stream(stream_final_response(final_response))

    st.session_state.messages.append({"role": "assistant", "content": final_response})