# Platform

How characters talk, find each other, get born and die. Owned by Clara.

## Run it

```bash
cp .env.example .env
docker compose up -d --wait
uv venv --python 3.12 && uv pip install -r requirements.txt
.venv/bin/python -m world.service --port 8100
```

Then start a run. With no `cast`, it uses `personas/starting_cast.json`.

```bash
curl -X POST localhost:8100/run -H 'content-type: application/json' -d '{"seed": "a coffee subscription"}'
```

No Docker? Set `TRANSPORT=http` and `REGISTRY=file` in `.env`. Everything works the same on one machine.

## Backends

| Setting | Values | Use |
| --- | --- | --- |
| `TRANSPORT` | `slim`, `http`, `local` | SLIM through the AGNTCY app-sdk, plain HTTP as the fallback, asyncio queues for single-process tests |
| `REGISTRY` | `agntcy`, `file`, `memory` | The AGNTCY Directory, a shared folder of JSON files as the fallback, an in-process dict for tests |

Agent code never changes between them.

## For Anthony: what a character process does

The world service starts each character as `python -m agents.character` from the repo root. It sets `AGENT_ID`, `PERSONA_FILE`, `RUN_ID` and `ODDTOWN_TOWN` on top of `.env`.

```python
from transport.town import Town
from transport.adapter import DeadPeerError

town = Town.from_env(agent_id)
await town.join({"kind": "character", "name": persona["name"], "persona": persona}, handler)

await town.send(peer_id, {"type": "idea.share", "body": {...}})  # raises DeadPeerError if the peer is gone
dead = await town.broadcast({"type": "endorse", "body": {...}})  # every living agent plus the world service
peers = await town.characters()  # every living character but you, with their personas
await town.leave()  # on death, after broadcasting agent.last_words
```

- `handler(envelope)` runs once per incoming message, after it has been acked. `send` and `broadcast` fill in `msg_id`, `from`, `to` and `sent_at`.
- A bounced send deletes the dead peer's record before it raises, so the next `characters()` no longer lists it. Catch `DeadPeerError` there to do the replacement pick.
- Exit the process after `leave()`. Exit code 0 reads as a death, anything else as a crash.

Messages your agents send to the world service, which is the record with `kind == "world"`:

| Type | Body | What happens |
| --- | --- | --- |
| `spawn.request` | `{"persona": {...}}`, with a unique `id` | The world starts the newcomer and replies `spawn.result` with `ok` |
| `usage.report` | `{"tokens": 1234}` | Counts toward the token budget |
| `human.request` | `{"kind": "...", ...}` | Shows up as a card in the UI. The answer comes back as a `world.event` broadcast with `kind: "human.answer"` |

`world.event` broadcasts have `kind` set to `seed`, `constraint`, `feedback`, `human.answer` or `stop`. On `stop`, call `leave()` and exit.

Finalists come from public broadcasts. `endorse` and `unendorse` carry `{"branch": {...}}` or `{"branch_id": ...}`, plus an optional `reason`. An `unendorse` reason, or an `objection` broadcast with `{"branch_id", "reason"}`, becomes a minority report candidate.

## For Oleg: world service endpoints

| Call | Does |
| --- | --- |
| `POST /run` `{"seed", "cast"?, "time_budget_s"?, "token_budget"?}` | Launches the cast, waits for everyone to join, broadcasts the seed |
| `POST /run/inject` `{"kind": "constraint", "text"}` | Broadcasts a world event |
| `POST /run/human/{request_id}/answer` `{"answer"}` | Answers a human request |
| `POST /run/agents/{id}/kill` | SIGKILLs a character for the crash demo |
| `POST /run/stop` | Ends the run now and returns the result |
| `GET /run` | State, living characters, endorsements, human requests, and the result once it has ended |

The world emits these observer events: `run.started`, `world.event`, `agent.died`, `agent.spawned`, `agent.crashed`, `agent.exited`, `agent.bounced`, `human.request`, `human.answer` and `run.ended`. `run.ended` carries `finalists` (with `branch`, `support`, `champions` and `minority_report`) and `ties`. `observer/events.py` is a stub that writes JSONL to `OBSERVER_LOG` until your real client replaces it.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Each check runs on `http+file` and on `slim+agntcy`. The SLIM and Directory cases are skipped if `docker compose` isn't up. `tests/fake_character.py` is a scripted character that stands in for the real one.
