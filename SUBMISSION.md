# HW5 — My LangGraph helpdesk

> **Models used.** The logs in `outputs/S01.json … S14.json` (and `outputs/tickets/`) come from a **real model: OpenAI `gpt-4o-mini`** via `langchain-openai` (`ChatOpenAI(...).bind_tools`), run with `python -m helpdesk.run_scenarios --model openai`. Part 1 (S01–S05) uses no model. Tests and CI use `FakeHelpdeskModel` ([helpdesk/fake_model.py](helpdesk/fake_model.py)), a clearly labelled deterministic rule-based stand-in, not an LLM; its earlier runs are kept in `outputs/fake/` for comparison. LLM wording is non-deterministic, so a rerun will word replies differently.

## Part 1 — How the graph works

### My graph and state

```text
START → read_request ──(route_original)──┬─ wifi_help     → END
                                         ├─ password_help → END
                                         └─ other_help    → END
```
State (`HelpState`): `message`, `category`, `reply`.

1. **Reads / changes.** `read_request` reads `message`, writes `category`. `wifi_help` / `password_help` read nothing needed (they look up `helpdesk.json`) and write `reply`. `other_help` writes the clarifying question as `reply`. The router reads `category` and writes nothing.
2. **Update vs. conditional edge.** A node returns a *state update* (a dict merged into the state). A conditional edge is a function that only returns the *name of the next node*; it changes no state.
3. **What the next node receives.** The current *whole graph state* (previous updates already merged), not just the previous node's update. In the trace, `wifi_help` can still see `message`.
4. **START / END / compile.** `START` and `END` are virtual entry and exit nodes. `compile()` validates the graph (edges, reachable nodes) and returns the runnable object; before that it is only a description.

### Predictions before running

| Message | Predicted route | Actual route (S01–S05 logs) | What I learned |
| --- | --- | --- | --- |
| I cannot connect to Wi-Fi. | wifi_help | read_request → wifi_help | `Wi-Fi` matches alias `wi-fi` |
| I forgot my PASSWORD. | password_help | read_request → password_help | lowercasing makes it case-insensitive |
| My laptop screen is broken. | other_help | read_request → other_help | anything unsupported falls to the question |
| My Wi-Fi password does not work. | wifi_help (Wi-Fi rule is checked first) | read_request → wifi_help | predictions matched all four runs |

For the mixed message, `category` becomes `password_wifi` (both topics found) but rule 1 (“contains wifi → `wifi_help`”) is evaluated first, so the user who really has a password problem receives Wi-Fi instructions. Trace S04: after `read_request` state = `{message, category: password_wifi}`; after `wifi_help` the `reply` is set ([outputs/S04.json](outputs/S04.json)).

### My change to the graph

```text
START → read_request ──(route_with_clarification)──┬─ wifi_help        → END
                                                   ├─ password_help    → END
                                                   ├─ other_help       → END
                                                   └─ clarify_problem  → END   (new: both topics)
```
New trace S05: `START → read_request → clarify_problem → END`, reply “Is the problem connecting to Wi-Fi, or recovering your account password? …” ([outputs/S05.json](outputs/S05.json)). The *route* changed (a new node), not just the final sentence. Single-topic messages still route as before (tested).

**What LangGraph controls:** running nodes in order, merging each returned update into the state, calling the conditional edge and following the name it returns, stopping at END. **What my Python decides:** how topics are detected (`detect_topics`), the routing rules inside `route_*`, the reply texts. No model is involved.

## Part 2 — Model and tools

```text
START → remember_preference → track_conversation → agent_model ─┬─ no tool_calls ───────────────→ END
                                                      ▲         ├─ tool_calls, rounds < 4 → run_tools ─┐
                                                      └─────────┴─ tool_calls, rounds ≥ 4 → limit_reached → END
                                                                                  (run_tools → agent_model)
```
Model requests tool → `run_tools` executes it → `ToolMessage` appended → model sees it and either requests another tool or answers.

Tools ([helpdesk/tools.py](helpdesk/tools.py)) are plain functions, each tested directly in `tests/test_tools.py`: `find_instructions(topic)`, `check_service_status(service, profile)`, `prepare_ticket(problem, steps_tried, …)` (refuses when problem or steps are missing; writes a local JSON using `ticket_template.json`; ticket id `TKT-<uuid>`).

**Direct calls (no agent, no model)** — actual output of `python` calling the functions (also tested in `tests/test_tools.py`):


**Loop limit.** `tool_rounds` is reset to 0 each turn and incremented by `run_tools`. The limit (`MAX_TOOL_ROUNDS = 4`) is enforced on the conditional edge `route_after_model`: with pending tool calls and `tool_rounds ≥ 4` it routes to `limit_reached` instead of `run_tools`, which answers `I stopped because I reached the limit…` and sets `limit_reached`. Tested with a model that never stops asking for tools.

**Actual multi-tool run (S06, second message, `normal`)**, from [outputs/S06.json](outputs/S06.json), model = OpenAI `gpt-4o-mini`:
1. model requests `check_service_status(service="wifi")` → result `status: operational` (source `service_status.json`)
2. model requests `prepare_ticket({"problem": "Unable to connect to the Campus-Demo Wi-Fi network after enabling Wi-Fi and forgetting the saved network.", "steps_tried": ["enabled Wi-Fi", "forgot the saved network"], "category": "wifi", "service_status": "operational"})` → local draft `TKT-….json` saved in [outputs/tickets/](outputs/tickets/) (`sent: false`)
3. final answer (quoted from the log): “The Wi-Fi service is operational, and I have prepared a local draft ticket regarding your issue with connecting to the Campus-Demo network.   **Ticket ID:** TKT-3dc44f4e   **Problem:** Unable to connect to the Campus-Demo Wi-Fi network after enabling Wi-Fi and forgetting the saved network.   **Steps Tried:** Enabled Wi-Fi, forgo”
The first S06 message used `find_instructions(topic="wifi")`; loop limit was not reached (`limit_reached: false`). Node order of the second message: remember_preference → track_conversation → agent_model → run_tools → agent_model → run_tools → agent_model.

Same request, different snapshot (real model, same text, only `--profile` differs):
- **S07 `wifi_outage`** → only `check_service_status` (outage); no ticket. Reply: “It appears that the demo Wi-Fi service is currently experiencing an outage across campus, and there is no estimated repair time available. Since the service is down, I cannot prepare a ticket for your connection issue at this moment.   Please try again later w”
- **S08 `status_unavailable`** → only the status call (unknown); no ticket. Reply: “The Wi-Fi service status is currently unknown. I cannot assume it is operating. Please check back later or try again to see if the service status becomes available. If you need further assistance, let me know!” (it did **not** offer a ticket draft as SCENARIOS.md's example does, but it correctly refused to assume the service is operating.)
- **S14 `recovery_outage`** → checks `account_recovery`, reports an outage, no repair time invented. Reply: “The password recovery service is currently experiencing an outage and is unavailable. Unfortunately, there is no information on when it will be repaired. If you need further assistance, feel free to ask!”

Answers:
1. **Run a tool or end?** The conditional edge `route_after_model` looks at the last AI message: has `tool_calls` (and the limit is not hit) → `run_tools`, otherwise END.
2. **Who requests / executes?** The model only *requests* (`AIMessage.tool_calls`). My node `run_tools` looks the function up and *executes* it, injecting `user_id`, `thread_id`, status profile and ticket directory from the config, so the model can't choose them.
3. **State change after a result.** `messages` gets one `ToolMessage` per call, `tool_rounds` increases by one, and `sources` records the data file used.
4. **Difference from Part 1.** In Part 1 my code picks the next node from keywords. Here the *model* decides what to request next after seeing results, so the path (status first, then maybe a ticket) differs per snapshot; my code only enforces the limit and executes tools. In the real runs (S06–S09, S14) `gpt-4o-mini` itself chose to check the status first and to ticket only when it was operational; the fake model only imitates that.

## Part 3 — Memory

Two separate stores:
- **Conversation memory** — `messages`, `topic`, `steps_tried` in graph state, saved by `SqliteSaver` (`checkpoints.sqlite3`) under a **`thread_id`**. A `thread_id` identifies one conversation (one saved state history). A **user id** identifies a person across conversations. They are independent: the same user can have many threads; a thread has no authority over whose preferences are loaded.
- **User memory** — `preferred_language` per `user_id` in `user_memory.sqlite3` ([helpdesk/memory.py](helpdesk/memory.py)), written by the `remember_preference` node when a message says “I prefer replies in X”. Nothing is loaded from `demo_users.json`.

Both IDs are given by the program (CLI flags) via the graph config; the model never selects them.

**Retrieval vs. memory.** Looking up Wi-Fi instructions is *retrieval*: general, user-independent knowledge read from the fixed reference file `helpdesk.json`. Recalling the language is *memory*: a fact about this user that was learned during interaction, didn't exist beforehand, and must be stored and scoped to that user.

| Situation | Command used | Log file | What happened |
| --- | --- | --- | --- |
| Refer to an earlier message | S10: two `chat` calls, thread `s10` | [S10.json](outputs/S10.json) | Second message “Which steps did I already try?” answered from checkpointed state (`enabled Wi-Fi, forgot the saved network, tried reconnecting`); source: conversation_state |
| Close, restart and resume | S11: separate processes, thread `restart-a` | [S11.json](outputs/S11.json) | New process, same storage; recalled Wi-Fi problem and steps without re-sending them |
| Same user, new conversation | S12: `preference-a1` then `preference-a2` (restart between) | [S12.json](outputs/S12.json) | Source `user_memory: READ preferred_language=Kazakh`; no conversation state carried (`topic=None`) |
| Different user | S13: `demo-user-b`, thread `isolation-b` | [S13.json](outputs/S13.json) | “No preferred language … no previous problem recorded”; no user_memory READ in sources |

With the real model, S11 and S12 answered in Kazakh using the stored preference (sources: `user_memory: READ`). In S12, `gpt-4o-mini` answered without calling a tool.

## Part 4 — Running and checking my work

See [README.md](README.md): setup, Python 3.12, pinned [requirements.txt](requirements.txt), where memory lives (`local-storage/`) and how to clear it, the table “what each test catches”, and the CI workflow [.github/workflows/tests.yml](.github/workflows/tests.yml) (runs `pytest` on every push with no key). Tests use the fake model; the saved scenario logs come from the real model (`--model openai`). 20 tests pass locally. GitHub Actions ran on push to https://github.com/ars512/css4007-hw5 and passed (workflow `tests`, 18 s).

## Choices and limitations

- **Storage:** SQLite for both stores (`langgraph-checkpoint-sqlite` as supplied; plain `sqlite3` for preferences) — zero setup, survives restarts, easy to delete.
- **Rule-based extraction** (topic, steps, language) is deliberately simple: it ignores negations (“I did *not* restart”), supports a few step phrases, and only recognises “prefer … in X” / “reply in X”.
- **Real model** was `gpt-4o-mini` only, one run per scenario; behaviour can vary between runs. In S09 the model chose to check the status before drafting, so the ticket records `operational` (this is real tool output, not invented). Kazakh quality was not reviewed by a native speaker.
- The fake model is used only in tests/CI.
- Ticket summary is a fixed sentence built from the category plus the user's extracted steps; no extra troubleshooting is invented.
- Repository: https://github.com/ars512/css4007-hw5 (public fork). 

## Supplied scenarios — my actual results

Command for all: `python -m helpdesk.run_scenarios --model openai` (model: gpt-4o-mini). Each log starts with scenario id, command, snapshot, user and thread.

| Scenario | Command or manual steps | Log file | What happened / what I changed |
| --- | --- | --- | --- |
| S01 — Simple Wi-Fi route | `cli part1 --message "I cannot connect to Wi-Fi."` | [S01](outputs/S01.json) | read_request → wifi_help; instructions returned |
| S02 — Case-insensitive password route | `cli part1 --message "I forgot my PASSWORD."` | [S02](outputs/S02.json) | → password_help |
| S03 — Unsupported request | `cli part1 …"My laptop screen is broken."` | [S03](outputs/S03.json) | → other_help, asks Wi-Fi or password |
| S04 — Two topics before the change | `cli part1 … --variant original` | [S04](outputs/S04.json) | → wifi_help (Wi-Fi rule wins; wrong for a password problem) → motivated S05 |
| S05 — Two topics after clarification | `cli part1 … --variant with_clarification` | [S05](outputs/S05.json) | → clarify_problem asks which problem first |
| S06 — Instructions then local ticket | 2× `cli chat --profile normal` | [S06](outputs/S06.json) | find_instructions; then check_service_status (operational) → prepare_ticket; draft in outputs/tickets (file name in the log) |
| S07 — Same request during outage | `cli chat --profile wifi_outage` | [S07](outputs/S07.json) | status = outage → no ticket, says no repair time is given |
| S08 — Status source unavailable | `cli chat --profile status_unavailable` | [S08](outputs/S08.json) | status unknown → no ticket; says it cannot assume operating; did not offer a draft (differs from the example wording, same decision) |
| S09 — Missing ticket details | 2× `cli chat` thread s09 | [S09](outputs/S09.json) | first: asked for problem and steps, no tool called; second: model checked status (operational) then prepared a draft with the user's steps; ticket records `operational` |
| S10 — Use the current conversation | 2× `cli chat` thread s10 | [S10](outputs/S10.json) | recalled the three steps from checkpointed state |
| S11 — Persist and recall after restart | `cli chat` ×2, separate processes | [S11](outputs/S11.json) | after process restart replied (in Kazakh, the saved preference) with the Wi-Fi problem and steps |
| S12 — Preference across threads without replay | `cli chat` ×2, new thread | [S12](outputs/S12.json) | `user_memory: READ preferred_language=Kazakh` in the new thread; reply in Kazakh; no tool called |
| S13 — Do not share memory between users | `cli chat` user a then user b | [S13](outputs/S13.json) | user b sees nothing of user a |
| S14 — Password recovery outage | `cli chat --profile recovery_outage` | [S14](outputs/S14.json) | looked up `account_recovery`, reported outage, no repair time |

**What I would change if wrong:** S04 shows the original behaviour is itself the “bug” fixed by S05. On the real model all 14 scenarios behaved as specified on the first run; the issues I fixed during development were Windows console encoding for Kazakh output, and machine-specific paths leaking into logs (now removed).
