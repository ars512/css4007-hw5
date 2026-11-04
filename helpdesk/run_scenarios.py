"""Run S01-S14 from data/scenarios.json and write one log per scenario to outputs/.

Every message event is executed in its OWN python process (python -m helpdesk.cli ...), so a
restart_process event is a real process restart against the same storage directory.

  python -m helpdesk.run_scenarios                 # all, fake model
  python -m helpdesk.run_scenarios --ids S06 S07   # some
  python -m helpdesk.run_scenarios --model openai  # needs OPENAI_API_KEY / MODEL_NAME
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .data import ROOT, load_json

OUT = ROOT / "outputs"
STORAGE = ROOT / "local-storage" / "scenarios"


def shown(cmd):
    """Command for the log, with machine-specific paths removed."""
    out, skip = ["python -m helpdesk.cli"], False
    for i, tok in enumerate(cmd[3:]):
        if skip:
            skip = False
            continue
        if tok in ("--trace-file", "--storage-dir", "--tickets-dir"):
            skip = True
            continue
        out.append('"%s"' % tok if " " in tok else tok)
    return " ".join(out)


def run_scenario(sc, model, out_dir):
    if sc.get("reset_before"):
        shutil.rmtree(STORAGE, ignore_errors=True)       # reset conversation AND user memory
    STORAGE.mkdir(parents=True, exist_ok=True)
    tickets = out_dir / "tickets"
    log = {"scenario": sc["id"], "title": sc["title"], "part": sc["part"],
           "status_profile": sc["status_profile"], "graph_variant": sc.get("graph_variant"),
           "reset_before": sc.get("reset_before", False), "events": []}
    for ev in sc["events"]:
        if ev["action"] == "restart_process":
            log["events"].append({"action": "restart_process",
                                  "note": "each message below runs in a new python process; storage kept"})
            continue
        with tempfile.TemporaryDirectory() as td:
            tf = str(Path(td) / "trace.json")
            if sc["part"] == 1:
                cmd = [sys.executable, "-m", "helpdesk.cli", "part1", "--message", ev["message"],
                       "--variant", sc.get("graph_variant", "original"), "--trace-file", tf]
            else:
                cmd = [sys.executable, "-m", "helpdesk.cli", "chat", "--user", ev["user_id"],
                       "--thread", ev["thread_id"], "--profile", sc["status_profile"],
                       "--message", ev["message"], "--model", model, "--storage-dir", str(STORAGE),
                       "--tickets-dir", str(tickets), "--trace-file", tf]
            proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
            if proc.returncode != 0:
                raise SystemExit("%s failed:\n%s" % (sc["id"], proc.stderr))
            trace = json.loads(Path(tf).read_text(encoding="utf-8"))
        entry = {"action": "message", "command": shown(cmd),
                 "user_id": ev["user_id"], "thread_id": ev["thread_id"], "message": ev["message"]}
        if sc["part"] == 1:
            entry.update(nodes=["START"] + trace["nodes"] + ["END"], steps=trace["steps"], reply=trace["reply"])
        else:
            entry.update(model=trace["model"], nodes=trace["nodes"], sources=trace["sources"],
                         trace=trace["trace"], reply=trace["reply"], limit_reached=trace["limit_reached"])
        log["events"].append(entry)
    (out_dir / (sc["id"] + ".json")).write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    return log


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--ids", nargs="*")
    p.add_argument("--model", default="fake", choices=["fake", "openai"])
    p.add_argument("--out", default=str(OUT))
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for sc in load_json("scenarios.json")["scenarios"]:
        if a.ids and sc["id"] not in a.ids:
            continue
        log = run_scenario(sc, a.model, out)
        last = [e for e in log["events"] if e["action"] == "message"][-1]
        print("%s  %s\n    -> %s" % (sc["id"], " -> ".join(last["nodes"]), last["reply"]))


if __name__ == "__main__":
    main()
