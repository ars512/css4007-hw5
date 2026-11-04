"""Part 1: rule-based helpdesk graph, no LLM.

Original:  START -> read_request -> (wifi_help | password_help | other_help) -> END
Changed:   a new clarify_problem node handles messages that mention both topics.
"""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .data import detect_topics, helpdesk


class HelpState(TypedDict, total=False):
    message: str    # the user's original message (never modified)
    category: str   # set by read_request
    reply: str      # set by a help node


def _format(topic):
    guide = helpdesk()["instructions"][topic]
    steps = " ".join("%d. %s" % (i, s) for i, s in enumerate(guide["steps"], 1))
    return "%s [%s]: %s" % (guide["title"], guide["instruction_id"], steps)


def read_request(state):
    topics = detect_topics(state["message"])
    return {"category": "_".join(sorted(topics)) if topics else "other"}


def wifi_help(state):
    return {"reply": _format("wifi")}


def password_help(state):
    return {"reply": _format("password")}


def other_help(state):
    return {"reply": "Is your problem about Wi-Fi or about a password?"}


def clarify_problem(state):
    return {"reply": "Is the problem connecting to Wi-Fi, or recovering your account password? "
                     "Tell me which one you want to solve first."}


def route_original(state):
    """Conditional edge: returns only the NAME of the next node; it changes no state."""
    cat = state["category"]
    if "wifi" in cat:          # rule 1 wins even when 'password' is also present
        return "wifi_help"
    if "password" in cat:
        return "password_help"
    return "other_help"


def route_with_clarification(state):
    if state["category"] == "password_wifi":   # both topics (sorted category name)
        return "clarify_problem"
    return route_original(state)


def build_graph(variant="original"):
    g = StateGraph(HelpState)
    g.add_node("read_request", read_request)
    g.add_node("wifi_help", wifi_help)
    g.add_node("password_help", password_help)
    g.add_node("other_help", other_help)
    g.add_edge(START, "read_request")
    if variant == "with_clarification":
        g.add_node("clarify_problem", clarify_problem)
        g.add_conditional_edges("read_request", route_with_clarification,
                                ["wifi_help", "password_help", "other_help", "clarify_problem"])
        g.add_edge("clarify_problem", END)
    elif variant == "original":
        g.add_conditional_edges("read_request", route_original,
                                ["wifi_help", "password_help", "other_help"])
    else:
        raise ValueError("unknown variant: %s" % variant)
    for n in ("wifi_help", "password_help", "other_help"):
        g.add_edge(n, END)
    return g.compile()


def run(message, variant="original"):
    """Run the graph; return node order and the full state after each node."""
    graph = build_graph(variant)
    state = {"message": message}
    steps = []
    for update in graph.stream({"message": message}, stream_mode="updates"):
        for node, change in update.items():
            state = {**state, **change}
            steps.append({"node": node, "update": change, "state_after": dict(state)})
    return {"variant": variant, "nodes": [s["node"] for s in steps], "steps": steps,
            "reply": state.get("reply")}
