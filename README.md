# HW5 — University IT helpdesk with LangGraph

CSS-4007 · fictional data only; no real tickets or university systems.
Explanations, drawings and the scenario table are in [SUBMISSION.md](SUBMISSION.md). Logs are in [outputs/](outputs/).

## Setup

Python **3.12** (developed on 3.12; needs ≥ 3.10 for the pinned langgraph).

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1            # macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
```

`requirements.txt` pins the exact versions used (langgraph 1.2.19, langgraph-checkpoint-sqlite 3.1.3, ...).

### Model API key (only for the real-model run)

```powershell
copy .env.example .env     # then set OPENAI_API_KEY and MODEL_NAME (a tool-capable model)
```
`.env` is git-ignored. **No key is needed** for Part 1, for the tests, or for `--model fake`.

## Run

Part 1 (no model):
```powershell
python -m helpdesk.cli part1 --message "My Wi-Fi password does not work."
python -m helpdesk.cli part1 --message "My Wi-Fi password does not work." --variant with_clarification
```

Parts 2–3 (model + tools + memory). `--user`, `--thread`, `--profile` are chosen by you, never by the model.
Profiles: `normal`, `wifi_outage`, `recovery_outage`, `status_unavailable`.
```powershell
python -m helpdesk.cli chat --user demo-user-a --thread t1 --profile normal --model fake --show-trace --message "Show me the Wi-Fi troubleshooting steps."
python -m helpdesk.cli chat --user demo-user-a --thread t1 --model fake          # interactive, empty line quits
python -m helpdesk.cli chat --user demo-user-a --thread t1 --model openai ...    # real model (needs .env)
```
Each invocation is a separate process, so running it again with the same `--user/--thread` resumes the saved conversation.

Run all scenarios S01–S14 (each message in its own process; writes `outputs/S01.json … S14.json` and `outputs/tickets/`):
```powershell
python -m helpdesk.run_scenarios                 # fake model
python -m helpdesk.run_scenarios --ids S06 S07
python -m helpdesk.run_scenarios --model openai  # real model, needs .env
```

## Where memory is stored / how to reset

Everything lives under `HELPDESK_STORAGE_DIR` (default `local-storage/`, git-ignored):

| File | Content |
| --- | --- |
| `checkpoints.sqlite3` | LangGraph checkpointer: conversation state per `thread_id` |
| `user_memory.sqlite3` | user preferences per `user_id` |
| `tickets/` | local ticket drafts (JSON) |

Reset: delete the folder (`Remove-Item -Recurse -Force local-storage`). The scenario runner uses `local-storage/scenarios/` and wipes it when `reset_before` is true.

## Tests and CI

```powershell
python -m pytest -q
```
Tests never need an API key; they use `FakeHelpdeskModel`, a deterministic rule-based stand-in (clearly not an LLM). CI: [.github/workflows/tests.yml](.github/workflows/tests.yml) runs the tests on every push.

| Test file | What it catches if the code breaks |
| --- | --- |
| `test_part1_routing.py` | wrong route (e.g. password before Wi-Fi), case sensitivity, clarify node not used, help text not from helpdesk.json, message overwritten |
| `test_tools.py` | wrong instructions, a status of "unknown" reported as operational, ticket written with invented/missing details, ticket marked as sent |
| `test_agent_and_memory.py::test_conditional_ticket_...` | ticket made during outage / unknown status (ignores tool results) |
| `…::test_tool_loop_limit_is_enforced` | infinite or over-long tool loop |
| `…::test_conversation_survives_restart` / `..._real_process_restart` | conversation not saved by the checkpointer or lost on restart |
| `…::test_preference_shared_across_threads_not_across_users` | preference not persisted, or leaking to another user |
| `…::test_new_thread_has_separate_conversation_state` | threads sharing history |
| `…::test_preference_not_loaded_from_fixture_file` | hard-coded preference |

## Layout

`helpdesk/part1.py` rule graph · `tools.py` three tools · `agent.py` model/tool graph + memory · `memory.py` user memory · `fake_model.py` test stand-in · `cli.py` entry point · `run_scenarios.py` S01–S14 runner.
