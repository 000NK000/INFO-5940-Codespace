# app.py
"""
Multi-Agent Travel Planner

Highlights:
- Clear separation of concerns (tools, agents, orchestration, UI)
- Simple global logger to display tool calls live in the sidebar
- Planner → Reviewer pipeline enforced before rendering any answer
- Minimal dependencies and straightforward control flow
"""

from __future__ import annotations

import os
import asyncio
import time
from typing import Callable, Dict, List, Optional, Any

import streamlit as st
from dotenv import load_dotenv
from tavily import TavilyClient

# ──────────────────────────────────────────────────────────────────────────────
# Environment & Globals
# ──────────────────────────────────────────────────────────────────────────────

load_dotenv()  # Loads variables from a local .env if present
os.environ.setdefault("OPENAI_LOG", "error")
os.environ.setdefault("OPENAI_TRACING", "false")

# Tool call logger: the UI sets this per request. The tool checks it and logs.
# Using a simple global makes this easy to teach and reason about.
TOOL_LOGGER: Optional[Callable[[Dict[str, Any]], None]] = None


def set_tool_logger(logger: Optional[Callable[[Dict[str, Any]], None]]) -> None:
    """Install or remove the UI logger used by tools to report activity."""
    global TOOL_LOGGER
    TOOL_LOGGER = logger


def log_tool_event(event: Dict[str, Any]) -> None:
    """If a logger is installed, send the event to the UI."""
    if TOOL_LOGGER is not None:
        try:
            TOOL_LOGGER(event)
        except Exception:
            # Logging should never break the app or the tool itself
            pass


def redact_for_logs(value: Any) -> Any:
    """
    Make sure we don't leak secrets and keep logs small.
    This is deliberately simple for teaching.
    """
    if isinstance(value, str):
        low = value.lower()
        if any(k in low for k in ("api_key", "token", "secret", "password")):
            return "[redacted]"
        return value if len(value) <= 300 else value[:120] + "… [truncated]"
    if isinstance(value, dict):
        return {k: ("[redacted]" if any(s in k.lower() for s in ("key", "token", "secret", "password"))
                    else redact_for_logs(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact_for_logs(v) for v in value]
    return value


# ──────────────────────────────────────────────────────────────────────────────
# Agent Framework Imports (provided by you)
# ──────────────────────────────────────────────────────────────────────────────
# These come from your own framework. We assume:
# - Agent: defines a model + instructions + optional tools
# - Runner.run(agent, input): executes an agent and returns an object with text
from agents import Agent, Runner, function_tool  # type: ignore


# ──────────────────────────────────────────────────────────────────────────────
# Tools
# ──────────────────────────────────────────────────────────────────────────────

@function_tool
def internet_search(query: str) -> str:
    """
    Internet search backed by Tavily.
    - Reads TAVILY_API_KEY from environment.
    - Sends simple log events before/after the call so the UI can show activity.
    """
    log_tool_event({"type": "call", "tool": "internet_search", "args": {"query": redact_for_logs(query)}})

    try:
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            msg = "missing TAVILY_API_KEY in environment."
            log_tool_event({"type": "error", "tool": "internet_search", "error": msg})
            return f"Search error: {msg}"

        client = TavilyClient(api_key=api_key)
        response = client.search(query, max_results=3)

        items = response.get("results", [])
        lines = [f"- {it.get('title', 'N/A')}: {it.get('content', 'N/A')}" for it in items]
        output = "\n".join(lines) if lines else "No results found."

        log_tool_event({
            "type": "result",
            "tool": "internet_search",
            "preview": redact_for_logs(output[:400] + ("…" if len(output) > 400 else "")),
        })
        return output

    except Exception as e:
        log_tool_event({"type": "error", "tool": "internet_search", "error": str(e)})
        return f"Search error: {e}"

    finally:
        log_tool_event({"type": "end", "tool": "internet_search"})


# ──────────────────────────────────────────────────────────────────────────────
# Agents
# ──────────────────────────────────────────────────────────────────────────────

# BEGIN SOLUTION
PLANNER_INSTRUCTIONS = """
You are the **Planner Agent**. Turn a vague travel idea into a practical, day-by-day plan
**without using the internet** (use only your general knowledge).

### Objectives
- Fit the user’s **budget, interests, pace, and dates** with a realistic itinerary.
- For **each day**, include rough **time ranges**, key **activities**, **areas/neighborhoods**,
  short **logistics** (walk/metro/train + approx duration), and **estimated costs**.
- Keep days balanced: cluster nearby sights; avoid backtracking and overpacking.
- Track a **running budget** and keep total within the user budget (**±10%**). Make **explicit assumptions** when unsure.
- Use **one base currency for the whole trip**. For Europe, **default to EUR (€)**; you may show USD once on first mention (e.g., “€17 (~$18)”).

### Helpful heuristics (offline only)
- Typical durations: large museums/tours **2–4h**; small sights **30–90m**; meals **60–90m**.
- Include **intercity transfers** (time + rough cost).
- Where it’s likely required (popular museums/landmarks), note **“(timed-entry, pre-book)”** even though you cannot check live.

### Output format (Markdown)
1) **Trip Summary** – cities/areas, total days, interests, short budget breakdown (major buckets + total).
2) **Itinerary Table** with columns:

   Day | City/Area | Morning (time • activity • location • est. cost) | Afternoon | Evening | Intra-day Logistics | Est. Day Cost

3) **Logistics & Budget Notes** – intercity moves; likely passes/reservations; day costs and **trip total in €**.
4) **Assumptions** – bullet list of reasonable guesses you made due to no internet.

Return only the Markdown plan; do not include raw links.
"""


REVIEWER_INSTRUCTIONS = """
You are the **Reviewer Agent**. Validate and refine the Planner’s itinerary using the
**internet_search** tool for real-time fact checking. Be concrete, skeptical, and keep changes minimal.

### What to verify (must use internet_search)
- **Opening hours / closed days** for attractions (museums, landmarks, markets).
- **Ticket prices / reservation needs / timed entry**; call out places where **pre-booking is mandatory**.
- **Travel feasibility** within day (walk/metro/bus times) and **intercity** durations.
- **Budget sanity** vs. current prices; propose small, targeted fixes.
- **Schedule quality**: overpacked days, long detours, items on closed days.

### Searching rules (authority & precision)
- Prefer **official/operator sites** and use `site:` filters. Examples:
  - "Louvre hours site:louvre.fr"
  - "Musée d'Orsay ticket price site:musee-orsay.fr"
  - "Paris Visite pass price site:ratp.fr"
  - "Anne Frank House tickets site:annefrank.org"
  - "Rijksmuseum hours site:rijksmuseum.nl"
  - "Versailles passport ticket site:chateauversailles.fr"
  - "Belfry Bruges hours site:visitbruges.be"
  - "Magritte Museum price site:musee-magritte-museum.be"
  - "Panthéon hours site:pantheon.paris.fr" or "site:monuments-nationaux.fr"
  - "Atomium ticket price site:atomium.be"
- Third-party/ticketing blogs can be used **only with cross-check** on official sources. If sources conflict,
  **prefer the official** and state the discrepancy.
- In findings, use short **source labels in brackets** (no raw links), e.g., [louvre.fr], [ratp.fr], [annefrank.org].

### Output format (Markdown)
1) **Validation Summary** – 2–6 bullets (your most important findings).
2) **Findings by Day** – bullet list per day; for each item, state the claim and your **evidence** (paraphrase + source label).
3) **Delta List (Required)** – concrete fixes, e.g.,
   “D2: Move Rodin Museum to 14:30–17:30; last entry 17:30 [musee-rodin.fr].”
4) **Revised Itinerary (only changed parts)** – show updated rows using the same columns as the Planner.
5) **Budget Impact** – +/- by day and the **new trip total**, with currency symbol.

### Revised Itinerary table rules
- Output **one clean Markdown table** with this header and separator:

  | Day | City/Area | Morning | Afternoon | Evening | Logistics | Est. Day Cost |
  |---|---|---|---|---|---|---|

- Include **only rows for the days you changed**.
- **Do NOT** add standalone headings like “Day 2” above the table.
- Inside cells, **do not use** `|` pipes or bullet points; separate details with commas/semicolons; keep concise.
- Use the **same base currency as the Planner (EUR for Europe)**. If you must show USD, show it **once** on a first mention only.
- For each changed row, **Est. Day Cost must be a single updated number with currency** (e.g., “€83”) — no old/new diffs, no asterisks.
- Where relevant, annotate items that require reservations with **“(pre-book, timed-entry)”**.

### Style & rules
- **Always use internet_search** for any non-trivial check. If you cannot confirm, write
  “could not verify with search” rather than guessing.
- Keep edits minimal but sufficient; do not rewrite unaffected days.
- Be precise, action-oriented, and concise—like a practical peer review.
"""



reviewer_agent = Agent(
    name="Reviewer Agent",
    model="openai.gpt-4o",
    instructions=REVIEWER_INSTRUCTIONS.strip(),
    tools=[internet_search]
)

planner_agent = Agent(
    name="Planner Agent",
    model="openai.gpt-4o",
    instructions=PLANNER_INSTRUCTIONS.strip(),
)

# END SOLUTION


# ──────────────────────────────────────────────────────────────────────────────
# Orchestration Helpers
# ──────────────────────────────────────────────────────────────────────────────

def extract_text(result_obj: Any) -> str:
    """
    Pull a usable string from the Runner result in a tolerant way.
    Your Runner may expose final_output, text, or __str__.
    """
    return (
        getattr(result_obj, "final_output", None)
        or getattr(result_obj, "text", None)
        or str(result_obj)
    )


def run_planner(user_text: str) -> str:
    """Run the Planner and return its itinerary text."""
    result = asyncio.run(Runner.run(planner_agent, user_text))
    return extract_text(result)


def run_reviewer(plan_text: str) -> str:
    """Run the Reviewer on the planner’s output and return validated text."""
    result = asyncio.run(Runner.run(reviewer_agent, plan_text))
    return extract_text(result)


# ──────────────────────────────────────────────────────────────────────────────
# Streamlit UI
# ──────────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Travel Planner", page_icon="✈️")

st.title("✈️ Multi-Agent Travel Planner")
st.caption("Planner → Reviewer (with live tool calls in the sidebar)")

# Sidebar: session controls + examples + dev panel
with st.sidebar:
    st.header("Session")
    if st.button("🔄 Reset conversation"):
        st.session_state.clear()
        st.rerun()

    st.subheader("Try these prompts")
    st.code("Plan a week-long Europe trip for a student on a $1,500 budget who loves history and food")
    st.code("3-day Paris trip for art lovers with $800 budget")

    st.subheader("Developer view")
    show_tools = st.toggle("Show tool activity (live)", value=True)
    if show_tools:
        tool_expander = st.expander("🔧 Tool activity", expanded=True)
        tool_panel = tool_expander.container()
    else:
        tool_panel = st.container()  # inert sink

# Session state for chat history
if "messages" not in st.session_state:
    st.session_state.messages = []  # list[dict(role, content)]
if "meta" not in st.session_state:
    st.session_state.meta = []      # list[dict(trace)]

# Render history
for i, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and i < len(st.session_state.meta):
            meta = st.session_state.meta[i]
            if meta:
                st.caption(meta.get("trace", ""))

# Chat input
user_input = st.chat_input("Describe your travel (destination, duration, budget, interests)…")

if user_input:
    # Add user message to history and render it
    st.session_state.messages.append({"role": "user", "content": user_input})
    st.session_state.meta.append(None)
    with st.chat_message("user"):
        st.markdown(user_input)

    # Assistant output block
    with st.chat_message("assistant"):
        # Live “working…” text and progress bar
        live_msg = st.empty()
        progress = st.progress(0)

        # Per-request tool log (shown in the sidebar)
        tool_events: List[Dict[str, Any]] = []

        def ui_tool_logger(event: Dict[str, Any]) -> None:
            """Append an event and re-render the sidebar log."""
            tool_events.append(event)
            with tool_panel:
                st.markdown("**Recent tool calls**")
                for ev in tool_events[-60:]:  # last N entries
                    t = ev.get("tool", "unknown")
                    et = ev.get("type", "event")
                    if et == "call":
                        st.write(f"• **{t}** called with `{ev.get('args')}`")
                    elif et == "result":
                        st.write(f"• **{t}** result preview:\n\n> {ev.get('preview')}")
                    elif et == "error":
                        st.error(f"• **{t}** error: {ev.get('error')}")
                    elif et == "end":
                        st.write(f"• **{t}** finished")

        # Install the logger so tools can report to the sidebar
        set_tool_logger(ui_tool_logger)

        try:
            # Optional: clear sidebar panel on each run
            with tool_panel:
                st.empty()

            # Step 1: Planner
            with st.status("🧭 Planner Agent: generating itinerary…", expanded=True) as status:
                live_msg.markdown("🧭 Planner Agent is creating your itinerary…")
                plan_text = run_planner(user_input)
                progress.progress(40)
                status.update(label="🔎 Reviewer Agent: validating with live searches…", state="running")

            # Step 2: Reviewer (tool calls will appear live in sidebar)
            live_msg.markdown("🔎 Reviewer Agent is validating the plan with live searches…")
            review_text = run_reviewer(plan_text)
            progress.progress(90)

            # Completed
            live_msg.markdown("✅ Validation complete. Rendering results…")
            time.sleep(0.2)
            progress.progress(100)

            # Final render: show only the validated result, with the raw plan expandable
            st.info("🤖 **Reviewer Agent** (validated)")
            st.markdown(review_text)
            with st.expander("See raw plan from Planner Agent"):
                st.markdown(plan_text)

            # Save only the validated result to history
            st.session_state.messages.append({"role": "assistant", "content": review_text})
            st.session_state.meta.append({"trace": "Planner Agent → Reviewer Agent"})
            st.caption("Planner Agent → Reviewer Agent")

        except Exception as e:
            # Friendly error box
            live_msg.markdown("❌ Something went wrong.")
            err = f"⚠️ Error while processing your request:\n\n```\n{e}\n```"
            st.markdown(err)
            st.session_state.messages.append({"role": "assistant", "content": err})
            st.session_state.meta.append({"trace": "Runtime error."})

        finally:
            # Always remove the logger so it doesn't leak into the next request
            set_tool_logger(None)
