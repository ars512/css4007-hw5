"""The three reusable tools. Each is a plain function that works without any agent."""
import json
import uuid
from pathlib import Path

from .data import helpdesk, service_status, storage_dir, ticket_template

SERVICES = ("wifi", "account_recovery")


def find_instructions(topic):
    """Look up Wi-Fi or password instructions in the local helpdesk data (retrieval)."""
    data = helpdesk()
    t = (topic or "").strip().lower()
    for name, aliases in data["topic_aliases"].items():
        if t == name or t in aliases or t.replace("-", "") == name:
            guide = data["instructions"][name]
            return {"found": True, "topic": name, "instruction_id": guide["instruction_id"],
                    "title": guide["title"], "steps": list(guide["steps"]),
                    "source": "data/helpdesk.json"}
    return {"found": False, "topic": t, "source": "data/helpdesk.json",
            "message": "No instructions for this topic. Supported topics: wifi, password."}


def check_service_status(service, profile="normal"):
    """Look up whether a service is operational, in outage, or unknown in a status snapshot."""
    s = (service or "").strip().lower()
    if s in ("password", "account recovery", "account-recovery"):
        s = "account_recovery"
    if s in ("wi-fi",):
        s = "wifi"
    profiles = service_status()["profiles"]
    entry = profiles.get(profile, {}).get(s)
    if entry is None:
        # unknown profile or service: we cannot say it is operating
        return {"service": s, "profile": profile, "status": "unknown",
                "message": "No status information is available.", "source": "data/service_status.json"}
    return {"service": s, "profile": profile, "status": entry["status"],
            "message": entry["message"], "source": "data/service_status.json"}


def prepare_ticket(problem, steps_tried, category=None, service_status=None, reference_ids=None,
                   user_id=None, thread_id=None, status_profile=None, tickets_dir=None):
    """Write a LOCAL ticket draft. Nothing is sent anywhere. Refuses to invent missing details."""
    steps = [s for s in (steps_tried or []) if s and s.strip()]
    missing = []
    if not problem or not problem.strip():
        missing.append("problem description")
    if not steps:
        missing.append("steps already tried")
    if missing:
        return {"ok": False, "missing": missing,
                "message": "Ask the user for: " + ", ".join(missing) + ". Do not invent them."}
    ticket = ticket_template()
    ticket.update({
        "ticket_id": "TKT-" + uuid.uuid4().hex[:8],
        "status": "draft",
        "user_id": user_id,
        "thread_id": thread_id,
        "category": category,
        "summary": problem.strip(),
        "steps_tried": steps,
        "service_status": service_status if service_status else "not checked",
        "status_profile": status_profile,
        "reference_ids": list(reference_ids or []),
    })
    out_dir = Path(tickets_dir) if tickets_dir else storage_dir() / "tickets"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (ticket["ticket_id"] + ".json")
    path.write_text(json.dumps(ticket, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "ticket_id": ticket["ticket_id"], "file": path.name,
            "sent": False, "message": "Local draft saved. Nothing was sent to a real helpdesk."}


TOOLS = {
    "find_instructions": find_instructions,
    "check_service_status": check_service_status,
    "prepare_ticket": prepare_ticket,
}

# Schemas shown to the model. user_id / thread_id / status profile are NOT here: the program
# injects them, so the model can never choose whose data or which snapshot is used.
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "find_instructions",
        "description": "Look up Wi-Fi or password instructions in the local helpdesk guide.",
        "parameters": {"type": "object", "properties": {
            "topic": {"type": "string", "enum": ["wifi", "password"]}}, "required": ["topic"]}}},
    {"type": "function", "function": {
        "name": "check_service_status",
        "description": "Check whether a service is operational, in outage, or unknown.",
        "parameters": {"type": "object", "properties": {
            "service": {"type": "string", "enum": list(SERVICES)}}, "required": ["service"]}}},
    {"type": "function", "function": {
        "name": "prepare_ticket",
        "description": "Prepare a LOCAL ticket draft. Only call when the problem and the steps the "
                       "user already tried are known; otherwise ask the user.",
        "parameters": {"type": "object", "properties": {
            "problem": {"type": "string"},
            "steps_tried": {"type": "array", "items": {"type": "string"}},
            "category": {"type": "string", "enum": ["wifi", "password"]},
            "service_status": {"type": "string", "description": "status you actually looked up, if any"}},
            "required": ["problem", "steps_tried"]}}},
]
