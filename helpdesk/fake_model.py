"""FakeHelpdeskModel: a deterministic, rule-based STAND-IN for a tool-calling LLM.

It is NOT a language model. It exists so tests, CI and the S01-S14 demonstrations run without an
API key. It has the same interface the graph needs (invoke(messages) -> AIMessage possibly with
tool_calls), and, like a real model, it only sees the message list: a system message carrying the
memory context, the conversation and earlier tool results. It never touches files or databases.
"""
import json
import re
import uuid

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

LABEL = "FakeHelpdeskModel (deterministic rule-based stand-in, not an LLM)"
SCOPE = "Wi-Fi troubleshooting, password recovery guidance, service status lookup, local ticket drafts"


def parse_context(system_text):
    ctx = {}
    for line in system_text.splitlines():
        m = re.match(r"^([A-Z_]+): (.*)$", line)
        if m:
            ctx[m.group(1)] = None if m.group(2) == "none" else m.group(2)
    return ctx


def _call(name, **args):
    return {"name": name, "args": args, "id": "call_" + uuid.uuid4().hex[:8], "type": "tool_call"}


def _label(topic):
    return {"wifi": "Wi-Fi", "password": "account password recovery"}.get(topic, "")


class FakeHelpdeskModel:
    label = LABEL

    def invoke(self, messages):
        system = next((m.content for m in messages if isinstance(m, SystemMessage)), "")
        ctx = parse_context(system)
        last_h = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
        text = messages[last_h].content
        low = text.lower()
        turn_tools = [m for m in messages[last_h + 1:] if isinstance(m, ToolMessage)]
        results = {m.name: json.loads(m.content) for m in turn_tools}
        topic = ctx.get("TOPIC")
        steps = [s for s in (ctx.get("STEPS_TRIED") or "").split("; ") if s]
        lang = ctx.get("PREFERRED_LANGUAGE")
        all_human = " ".join(m.content.lower() for m in messages if isinstance(m, HumanMessage))
        wants_ticket = "ticket" in all_human
        conditional = bool(re.search(r"\bif\b.*\boperat", low))

        # ---- second step of a turn: react to tool results --------------------------------
        if turn_tools:
            reply = self._after_tools(results, topic, steps, conditional, wants_ticket, low)
            if "prefer" in low and lang and not reply.tool_calls:
                reply = AIMessage(content=self._ack_pref(lang) + " " + reply.content)
            return reply

        # ---- first step of a turn --------------------------------------------------------
        asks_lang = "language" in low and ("which" in low or "have i" in low or "what" in low)
        asks_recall = bool(re.search(r"which (problem|steps)|what (steps|problem)|steps did i", low))
        if asks_lang or asks_recall or re.search(r"previous problem", low):
            return AIMessage(content=self._recall(low, ctx, topic, steps, lang, asks_lang, asks_recall))

        if re.search(r"\b(check|status|down|outage|known problem)\b", low) and topic:
            service = "account_recovery" if topic == "password" else "wifi"
            return AIMessage(content="", tool_calls=[_call("check_service_status", service=service)])

        if "ticket" in low or (wants_ticket and ("issue is" in low or steps)):
            if not topic or not steps:
                missing = []
                if not topic:
                    missing.append("what problem you are having")
                if not steps:
                    missing.append("what you have already tried")
                return AIMessage(content="I need a little more information first: " + " and ".join(missing)
                                 + "? What problem are you having, and what have you already tried?")
            return AIMessage(content="", tool_calls=[self._ticket_call(topic, steps, None)])

        if re.search(r"remember .*prefer|prefer", low) and not topic:
            return AIMessage(content=self._ack_pref(lang))

        if topic:
            return AIMessage(content="", tool_calls=[_call("find_instructions", topic=topic)])

        return AIMessage(content="I can only help with: %s. Is your problem about Wi-Fi or a password?" % SCOPE)

    # ------------------------------------------------------------------------------------
    def _ticket_call(self, topic, steps, status):
        ref = {"wifi": "GUIDE-WIFI", "password": "GUIDE-PASSWORD"}[topic]
        return _call("prepare_ticket",
                     problem="User reports an unresolved %s problem." % _label(topic),
                     steps_tried=steps, category=topic, service_status=status,
                     reference_ids=[ref])

    def _after_tools(self, results, topic, steps, conditional, wants_ticket, low):
        if "prepare_ticket" in results:
            r = results["prepare_ticket"]
            if not r.get("ok"):
                return AIMessage(content="I could not prepare the ticket yet. " + r.get("message", ""))
            st = results.get("check_service_status")
            status_txt = ("The demo status source reports %s is %s. " % (st["service"], st["status"])
                          if st else "The service status was not checked. ")
            return AIMessage(content="%sI prepared a local draft (%s, file %s) using the steps you told "
                                     "me you tried. Nothing has been sent." %
                             (status_txt, r["ticket_id"], r["file"]))
        if "check_service_status" in results:
            st = results["check_service_status"]
            if conditional and "ticket" in low:
                if st["status"] == "operational":
                    status = "%s: operational" % st["service"]
                    return AIMessage(content="", tool_calls=[self._ticket_call(topic, steps, status)])
                if st["status"] == "outage":
                    return AIMessage(content="The demo status source reports a %s outage. Your request said to "
                                             "prepare a ticket if the service was operating, so I have not "
                                             "prepared that ticket. The data does not give a repair time." %
                                     st["service"])
                return AIMessage(content="I could not determine the %s status. I cannot confirm that it is "
                                         "operating. Would you like a ticket draft explaining that the status "
                                         "could not be checked?" % st["service"])
            if st["status"] == "operational":
                return AIMessage(content="The demo status source reports %s is operating. %s" %
                                 (st["service"], st["message"]))
            if st["status"] == "outage":
                return AIMessage(content="Yes, the demo status source reports a known %s outage. %s I will not "
                                         "guess a repair time." % (st["service"], st["message"]))
            return AIMessage(content="I could not determine the %s status. %s" % (st["service"], st["message"]))
        if "find_instructions" in results:
            r = results["find_instructions"]
            if not r.get("found"):
                return AIMessage(content=r["message"])
            body = " ".join("%d. %s" % (i, s) for i, s in enumerate(r["steps"], 1))
            return AIMessage(content="%s [%s]: %s" % (r["title"], r["instruction_id"], body))
        return AIMessage(content="Done.")

    def _ack_pref(self, lang):
        return "Noted: I will reply in %s from now on." % lang if lang else "Which language do you prefer?"

    def _recall(self, low, ctx, topic, steps, lang, asks_lang, asks_recall):
        parts = []
        if asks_lang or "previous problem" in low:
            if lang:
                parts.append(("Сіз %s тілінде жауап беруді сұрадыңыз (Kazakh). " % "қазақ"
                              if lang.lower() == "kazakh" else "You asked me to reply in %s. " % lang))
            else:
                parts.append("No preferred language has been recorded for you. ")
        if "previous problem" in low:
            parts.append("No previous problem has been recorded for you. " if not topic else
                         "You reported a %s problem. " % _label(topic))
        if asks_recall:
            if topic:
                tried = ", ".join(steps) if steps else "nothing yet"
                parts.append("You reported a %s problem and said you tried: %s. " % (_label(topic), tried))
            else:
                parts.append("No problem has been recorded in this conversation. ")
        if "what can you help" in low:
            parts.append("I can help with: %s." % SCOPE)
        return "".join(parts).strip()
