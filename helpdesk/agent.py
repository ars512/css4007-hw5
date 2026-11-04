"""Parts 2-3: model + tools + memory.

Graph:
  START -> remember_preference -> track_conversation -> agent_model
  agent_model --(tool calls, rounds < MAX)--> run_tools -> agent_model      (the loop)
  agent_model --(tool calls, rounds >= MAX)--> limit_reached -> END
  agent_model --(no tool calls)--> END

* The MODEL only requests tools (AIMessage.tool_calls). run_tools executes them.
* user_id / thread_id / status profile come from the config set by the program, never from the model.
"""
import json
import os
import re
import sqlite3
from typing import Annotated, Optional, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from . import fake_model
from .data import detect_topics, storage_dir
from .memory import UserMemory, extract_language
from .tools import TOOL_SCHEMAS, TOOLS

MAX_TOOL_ROUNDS = 4

SYSTEM_PROMPT = """You are a university IT helpdesk assistant (fictional practice data).
Scope: Wi-Fi troubleshooting, password recovery guidance, service status lookup, local ticket drafts.
Tools: find_instructions, check_service_status, prepare_ticket. Use tool results, do not invent facts.
If the user makes an action conditional on a service status, check the status first and obey the result.
If the status is unknown, say so and ask; never treat unknown as operating. Never invent a repair time.
Only prepare a ticket when the problem and the steps already tried are known; otherwise ask.
Tickets are local drafts; never claim anything was sent. Never ask for passwords or recovery codes.
Reply in the user's preferred language if the context names one."""

STEP_PATTERNS = [
    (r"\b(enabled|turned on|switched on)\b", "enabled Wi-Fi"),
    (r"forg[eo]t the saved network", "forgot the saved network"),
    (r"reconnect|tried connecting|connect(?:ing)? again", "tried reconnecting"),
    (r"restart", "restarted the device"),
]


class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]   # conversation memory (checkpointed per thread_id)
    topic: Optional[str]                      # wifi / password
    steps_tried: list                         # steps the user said they already tried
    language: Optional[str]                   # copy of user memory loaded for this turn
    tool_rounds: int                          # loop counter, reset every turn
    limit_reached: bool
    sources: list                             # where information came from (for traces)


# ---------------------------------------------------------------------------------------
def extract_steps(text):
    low = text.lower()
    return [label for pat, label in STEP_PATTERNS if re.search(pat, low)]


def build_app(model, storage=None, tickets_dir=None, max_tool_rounds=MAX_TOOL_ROUNDS):
    sdir = storage_dir(storage)
    memory = UserMemory(sdir / "user_memory.sqlite3")
    conn = sqlite3.connect(str(sdir / "checkpoints.sqlite3"), check_same_thread=False)
    saver = SqliteSaver(conn)
    tdir = tickets_dir or str(sdir / "tickets")

    def remember_preference(state, config):
        user_id = config["configurable"]["user_id"]
        text = state["messages"][-1].content
        sources = []
        lang = extract_language(text)
        if lang:
            memory.set(user_id, "preferred_language", lang)
            sources.append("user_memory: WROTE preferred_language=%s" % lang)
        saved = memory.get(user_id, "preferred_language")
        if saved:
            sources.append("user_memory: READ preferred_language=%s" % saved)
        return {"language": saved, "sources": sources}

    def track_conversation(state):
        text = state["messages"][-1].content
        topics = detect_topics(text)
        topic = state.get("topic")
        if topics:
            topic = "wifi" if "wifi" in topics else topics[0]
        steps = list(state.get("steps_tried") or [])
        for s in extract_steps(text):
            if s not in steps:
                steps.append(s)
        return {"topic": topic, "steps_tried": steps,
                "sources": ["conversation_state: topic=%s steps_tried=%s" % (topic, steps)]}

    def context_message(state):
        return SystemMessage(content=SYSTEM_PROMPT + "\n\n[HELPDESK CONTEXT]\n"
                             "PREFERRED_LANGUAGE: %s\nTOPIC: %s\nSTEPS_TRIED: %s" % (
                                 state.get("language") or "none", state.get("topic") or "none",
                                 "; ".join(state.get("steps_tried") or []) or "none"))

    def agent_model(state):
        reply = model.invoke([context_message(state)] + list(state["messages"]))
        return {"messages": [reply]}

    def route_after_model(state):
        last = state["messages"][-1]
        if not getattr(last, "tool_calls", None):
            return END
        # The loop limit is enforced HERE, on the conditional edge.
        return "run_tools" if state.get("tool_rounds", 0) < max_tool_rounds else "limit_reached"

    def run_tools(state, config):
        cfg = config["configurable"]
        out, sources = [], []
        for call in state["messages"][-1].tool_calls:
            fn = TOOLS.get(call["name"])
            args = dict(call["args"])
            if fn is None:
                result = {"error": "unknown tool %s" % call["name"]}
            else:
                try:
                    if call["name"] == "check_service_status":
                        args["profile"] = cfg["status_profile"]
                    if call["name"] == "prepare_ticket":
                        args.update(user_id=cfg["user_id"], thread_id=cfg["thread_id"],
                                    status_profile=cfg["status_profile"], tickets_dir=tdir)
                    result = fn(**args)
                except TypeError as exc:
                    result = {"error": "bad arguments: %s" % exc}
            if "source" in result:
                sources.append("%s: %s" % (result["source"], call["name"]))
            out.append(ToolMessage(content=json.dumps(result, ensure_ascii=False),
                                   tool_call_id=call["id"], name=call["name"]))
        return {"messages": out, "tool_rounds": state.get("tool_rounds", 0) + 1, "sources": sources}

    def limit_reached(state):
        pending = [ToolMessage(content=json.dumps({"error": "not executed: tool loop limit reached"}),
                               tool_call_id=c["id"], name=c["name"])
                   for c in state["messages"][-1].tool_calls]
        final = AIMessage(content="I stopped because I reached the limit of %d tool rounds for one "
                                  "request. Please rephrase or try again." % max_tool_rounds)
        return {"messages": pending + [final], "limit_reached": True}

    g = StateGraph(AgentState)
    for name, fn in [("remember_preference", remember_preference), ("track_conversation", track_conversation),
                     ("agent_model", agent_model), ("run_tools", run_tools), ("limit_reached", limit_reached)]:
        g.add_node(name, fn)
    g.add_edge(START, "remember_preference")
    g.add_edge("remember_preference", "track_conversation")
    g.add_edge("track_conversation", "agent_model")
    g.add_conditional_edges("agent_model", route_after_model, ["run_tools", "limit_reached", END])
    g.add_edge("run_tools", "agent_model")
    g.add_edge("limit_reached", END)
    graph = g.compile(checkpointer=saver)

    class App:
        pass

    app = App()
    app.graph, app.memory, app.conn, app.storage = graph, memory, conn, sdir

    def close():
        memory.close()
        conn.close()
    app.close = close
    return app


def _summarize(node, update):
    s = {}
    for m in update.get("messages", []):
        if isinstance(m, AIMessage) and m.tool_calls:
            s.setdefault("model_requests", []).extend({"tool": c["name"], "args": c["args"]} for c in m.tool_calls)
        elif isinstance(m, AIMessage):
            s["model_answer"] = m.content
        elif isinstance(m, ToolMessage):
            s.setdefault("tool_results", []).append({"tool": m.name, "result": json.loads(m.content)})
    for k in ("topic", "steps_tried", "language", "tool_rounds", "limit_reached", "sources"):
        if k in update and update[k] not in (None, [], ""):
            s[k] = update[k]
    return s


def run_turn(app, user_id, thread_id, profile, text):
    """One user message. user_id/thread_id/profile are supplied by the program, not the model."""
    config = {"configurable": {"thread_id": thread_id, "user_id": user_id, "status_profile": profile},
              "recursion_limit": 40}
    inp = {"messages": [HumanMessage(content=text)], "tool_rounds": 0, "limit_reached": False}
    trace, sources, final = [], [], None
    for upd in app.graph.stream(inp, config, stream_mode="updates"):
        for node, change in upd.items():
            summ = _summarize(node, change)
            trace.append({"node": node, "summary": summ})
            sources.extend(summ.get("sources", []))
            for m in change.get("messages", []):
                if isinstance(m, AIMessage) and not m.tool_calls:
                    final = m.content
    state = app.graph.get_state(config).values
    return {"user_id": user_id, "thread_id": thread_id, "status_profile": profile, "message": text,
            "trace": trace, "nodes": [t["node"] for t in trace], "sources": sources, "reply": final,
            "limit_reached": bool(state.get("limit_reached"))}


def model_label(model):
    return getattr(model, "label", None) or "OpenAI chat model %s" % os.environ.get("MODEL_NAME", "?")


def make_model(kind):
    if kind == "fake":
        return fake_model.FakeHelpdeskModel()
    if kind == "openai":
        from langchain_openai import ChatOpenAI
        name = os.environ.get("MODEL_NAME")
        if not name or not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("Set OPENAI_API_KEY and MODEL_NAME in .env to use --model openai")
        return ChatOpenAI(model=name, temperature=0).bind_tools(TOOL_SCHEMAS)
    raise ValueError(kind)
