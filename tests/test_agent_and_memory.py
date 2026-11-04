"""Agent tests use FakeHelpdeskModel (a labelled, deterministic stand-in): no API key needed."""
import sys
import subprocess
from pathlib import Path

from langchain_core.messages import AIMessage

from helpdesk import agent
from helpdesk.fake_model import FakeHelpdeskModel

COND = ("I enabled Wi-Fi and forgot the saved network, but Campus-Demo still will not connect. "
        "Check whether Wi-Fi is down. If it is operating, prepare a ticket about this problem.")
ROOT = Path(__file__).resolve().parent.parent


def turn(storage, user, thread, text, profile="normal", model=None):
    app = agent.build_app(model or FakeHelpdeskModel(), storage=str(storage), tickets_dir=str(storage / "t"))
    try:
        return agent.run_turn(app, user, thread, profile, text)
    finally:
        app.close()   # simulates the process ending


def tool_names(res):
    return [r["tool"] for t in res["trace"] for r in t["summary"].get("tool_results", [])]


def test_conditional_ticket_depends_on_status_snapshot(tmp_path):
    normal = turn(tmp_path, "demo-user-a", "n", COND, "normal")
    assert tool_names(normal) == ["check_service_status", "prepare_ticket"]
    assert list((tmp_path / "t").glob("TKT-*.json"))
    outage = turn(tmp_path, "demo-user-a", "o", COND, "wifi_outage")
    assert tool_names(outage) == ["check_service_status"] and "not prepared" in outage["reply"]
    unknown = turn(tmp_path, "demo-user-a", "u", COND, "status_unavailable")
    assert tool_names(unknown) == ["check_service_status"] and "Would you like" in unknown["reply"]
    assert len(list((tmp_path / "t").glob("TKT-*.json"))) == 1   # only the normal run drafted


def test_missing_ticket_details_asks_instead_of_inventing(tmp_path):
    r = turn(tmp_path, "demo-user-a", "m", "Prepare a support ticket.")
    assert tool_names(r) == [] and "already tried" in r["reply"]


class LoopingModel:
    label = "LoopingModel (test double that never stops asking for tools)"

    def invoke(self, messages):
        return AIMessage(content="", tool_calls=[{"name": "find_instructions", "args": {"topic": "wifi"},
                                                  "id": "c%d" % len(messages), "type": "tool_call"}])


def test_tool_loop_limit_is_enforced(tmp_path):
    r = turn(tmp_path, "demo-user-a", "loop", "wifi", model=LoopingModel())
    assert r["limit_reached"] is True
    assert r["nodes"].count("run_tools") == agent.MAX_TOOL_ROUNDS
    assert "limit" in r["reply"]


def test_conversation_survives_restart(tmp_path):
    turn(tmp_path, "demo-user-a", "restart-a", "My issue is Wi-Fi. I enabled it and tried reconnecting, but it still fails.")
    r = turn(tmp_path, "demo-user-a", "restart-a", "Which problem and steps did we discuss?")   # new app object
    assert "Wi-Fi" in r["reply"] and "enabled Wi-Fi" in r["reply"] and "reconnecting" in r["reply"]
    assert any("conversation_state" in s for s in r["sources"])


def test_conversation_survives_real_process_restart(tmp_path):
    base = [sys.executable, "-m", "helpdesk.cli", "chat", "--user", "demo-user-a", "--thread", "p",
            "--storage-dir", str(tmp_path), "--tickets-dir", str(tmp_path / "t")]
    subprocess.run(base + ["--message", "Wi-Fi: I enabled it and tried reconnecting."], cwd=ROOT, check=True,
                   capture_output=True)
    out = subprocess.run(base + ["--message", "Which problem and steps did we discuss?"], cwd=ROOT, check=True,
                         capture_output=True, text=True, encoding="utf-8").stdout
    assert "enabled Wi-Fi" in out


def test_preference_shared_across_threads_not_across_users(tmp_path):
    turn(tmp_path, "demo-user-a", "a1", "Please remember that I prefer replies in Kazakh. I have a Wi-Fi problem.")
    same_user = turn(tmp_path, "demo-user-a", "a2", "Which language did I ask you to use?")
    assert "Kazakh" in same_user["reply"] and any("user_memory: READ" in s for s in same_user["sources"])
    other = turn(tmp_path, "demo-user-b", "b1", "Have I told you a preferred language or a previous problem?")
    assert "Kazakh" not in other["reply"] and "No preferred language" in other["reply"]
    assert not any("user_memory: READ" in s for s in other["sources"])


def test_new_thread_has_separate_conversation_state(tmp_path):
    turn(tmp_path, "demo-user-a", "t1", "My issue is Wi-Fi. I enabled it.")
    r = turn(tmp_path, "demo-user-a", "t2", "Which problem and steps did we discuss?")
    assert "No problem has been recorded" in r["reply"]


def test_preference_not_loaded_from_fixture_file(tmp_path):
    r = turn(tmp_path, "demo-user-a", "x", "Which language did I ask you to use?")
    assert "No preferred language" in r["reply"]
