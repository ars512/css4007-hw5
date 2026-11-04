"""Terminal entry point.

  python -m helpdesk.cli part1 --message "I cannot connect to Wi-Fi." [--variant with_clarification]
  python -m helpdesk.cli chat --user demo-user-a --thread t1 --profile normal --message "..."
  python -m helpdesk.cli chat --user demo-user-a --thread t1          (interactive)
"""
import argparse
import json
import sys


def _print_trace(trace):
    for step in trace:
        print("  [%s] %s" % (step["node"], json.dumps(step["summary"], ensure_ascii=False)))


def main(argv=None):
    p = argparse.ArgumentParser(prog="helpdesk")
    sub = p.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("part1", help="rule-based graph, no model")
    p1.add_argument("--message", required=True)
    p1.add_argument("--variant", default="original", choices=["original", "with_clarification"])
    p1.add_argument("--trace-file")

    c = sub.add_parser("chat", help="model + tools + memory")
    c.add_argument("--user", required=True, help="user identifier (chosen by YOU, never by the model)")
    c.add_argument("--thread", required=True, help="conversation identifier")
    c.add_argument("--profile", default="normal",
                   choices=["normal", "wifi_outage", "recovery_outage", "status_unavailable"])
    c.add_argument("--message", help="single message; omit for interactive mode")
    c.add_argument("--model", default="fake", choices=["fake", "openai"])
    c.add_argument("--storage-dir")
    c.add_argument("--tickets-dir")
    c.add_argument("--trace-file")
    c.add_argument("--show-trace", action="store_true")
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # Kazakh text on Windows consoles

    if a.cmd == "part1":
        from . import part1
        res = part1.run(a.message, a.variant)
        print("nodes:", " -> ".join(["START"] + res["nodes"] + ["END"]))
        for s in res["steps"]:
            print("  after %-15s state=%s" % (s["node"], json.dumps(s["state_after"], ensure_ascii=False)))
        print("reply:", res["reply"])
        if a.trace_file:
            with open(a.trace_file, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
        return 0

    from dotenv import load_dotenv
    load_dotenv()
    from . import agent
    model = agent.make_model(a.model)
    app = agent.build_app(model, storage=a.storage_dir, tickets_dir=a.tickets_dir)
    try:
        results = []

        def turn(text):
            res = agent.run_turn(app, a.user, a.thread, a.profile, text)
            if a.show_trace:
                _print_trace(res["trace"])
            print("helpdesk>", res["reply"])
            results.append(res)

        if a.message:
            turn(a.message)
        else:
            print("user=%s thread=%s profile=%s (empty line to quit)" % (a.user, a.thread, a.profile))
            while True:
                text = input("you> ").strip()
                if not text:
                    break
                turn(text)
        if a.trace_file and results:
            out = dict(results[-1], model=agent.model_label(model))
            with open(a.trace_file, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
    finally:
        app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
