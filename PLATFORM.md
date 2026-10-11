# Platform

How characters talk, find each other, get born and die. Owned by Clara.

## Run it

```bash
cp .env.example .env
docker compose up -d --wait
uv venv --python 3.12 && uv pip install -r requirements.txt
.venv/bin/uvicorn observer.app:app --port 8100
.venv/bin/python -m world.service --port 8200
```

The UI talks to the world service on 8200 and the observer on 8100. To start a run without the UI (with no `cast`, it uses `personas/starting_cast.json`):

```bash
curl -X POST localhost:8200/runs -H 'content-type: application/json' -d '{"seed": "a coffee subscription"}'
curl -X POST localhost:8200/runs/<run_id>/start
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
- Your agents emit `agent.died` with `picked` themselves, through `observer.client`. The world service doesn't.

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
| `POST /runs` `{"seed", "cast"?, "time_budget_s"?, "token_budget"?}` | Returns `{run_id}` right away, launches the cast, emits `run.created`, one `agent.born` per character as it joins, then `run.ready` |
| `POST /runs/{run_id}/start` | Once ready: broadcasts the seed with its `br_seed` branch, emits `run.started`, `world.event` and `branch.created` |
| `POST /runs/{run_id}/inject` `{"kind": "constraint" or "feedback", "text"}` | Broadcasts a world event |
| `POST /runs/{run_id}/human/{request_id}/answer` `{"answer"}` | Answers a human request |
| `POST /runs/{run_id}/agents/{id}/kill` | SIGKILLs a character for the crash demo |
| `POST /runs/{run_id}/stop` | Ends the run now and returns the result |
| `GET /runs/{run_id}` | State (`preparing`, `ready`, `running`, `ending`, `ended` or `failed`), living characters, endorsements, human requests, and the result once it's over |

Everything the world emits goes through `observer.client` and passes your contract. That covers `run.created`, `agent.born` (newcomers add `requested_by`), `run.ready`, `run.started`, `world.event`, `branch.created` for the seed, and `run.ended`. `run.ended` has `baseline: []` until Anthony's baseline exists, plus extra fields: `support`, `reasons` and `minority_report_by` on each finalist, and `reason`, `ties` and `living` on the payload. It also emits types your contract doesn't model: `agent.crashed`, `agent.exited`, `agent.bounced`, `human.request` and `human.answer`.

The world doesn't emit `agent.died`, because only the dying agent knows who it `picked`. Stopping a run isn't a `world.event` either, because your contract only allows `seed`, `constraint` and `feedback`. Add `stop` to it if the UI should show one.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Each check runs on `http+file` and on `slim+agntcy`. The SLIM and Directory cases are skipped if `docker compose` isn't up. The world tests start the real observer and check that every event passes its contract. This also runs Oleg's `observer/tests`. `tests/fake_character.py` is a scripted character that stands in for the real one.

## Ideation voting loop

A second run mode, alongside the town. Pass `"mode": "ideation_loop"` to `POST /runs`. The default, `"town"`, is unchanged.

```bash
curl -X POST localhost:8200/runs -H 'content-type: application/json' \
  -d '{"seed": "a coffee subscription", "mode": "ideation_loop", "loop": {"agents": 6, "rounds": 3, "top_n": 3}}'
```

| `loop` field | Range | Default |
| --- | --- | --- |
| `agents` | 3 to 12, taken from the top of `personas/extended_cast.json` | 6 |
| `rounds` | 3 to 10, counting round 1 | 3 |
| `top_n` | 3 to 6, the ideas that survive each round | 3 |
| `phase_timeout_s` | How long a phase waits for slow agents | 180 |
| `random_seed` | Seeds the random fill and who retires, so a run can be replayed | drawn per run |
| `replace_each_round` | One agent dies and is replaced between rounds | `true` |
| `replace_timeout_s` | How long the next round waits for a designed newcomer before falling back | 240 |

How a run goes, with A agents and N survivors:

1. **Round 1:** each agent writes one idea and broadcasts it, and every other agent twists it. That's A + A(A−1) = A² ideas. Each agent votes once, over every idea it didn't last edit: A(A−1) of them.
2. **Rounds 2 to R:** every agent twists all N survivors, making A·N new ideas that replace the old ones. Each agent votes over the N(A−1) it didn't last edit.
3. **Each round's top N:** whole vote levels go in while they fit, and the rest are drawn at random from the level that doesn't fit, ideas with 0 votes included.
4. **The answer:** the last round's N survivors become `run.ended`'s finalists.

**Deaths and replacements, between rounds only.** After every round but the last, one agent dies and a replacement joins before the next round starts, so there are always A agents and nobody dies mid-round.
- **Who dies:** if an agent crashed during the round, that crash is the round's death. Otherwise the world draws one agent at random (seeded) and sends it `ideation.retire`.
- **The replacement:** the same mechanics as the town, shared through `agents/design.py`. The dying agent says its last words and picks 3 random survivors (for a crash, a random survivor picks). One proposes, two refine, and the last one runs the checks and asks the world to start the newcomer, with a briefing on the seed, constraints and current survivors.
- **If the design fails:** a persona that fails the content check gets one more refine to fix it. If it fails again, or nothing joins within `replace_timeout_s`, the world starts an unused persona from `extended_cast.json` instead. A late design for a filled slot is refused, so the loop never gains an extra agent.

The world service runs the clock, counts votes and draws who retires (`world/ideation.py`). The agents write, twist, vote and design replacements (`agents/ideation.py`), each through its own persona and model.

Events for the UI:
- `run.started` and `run.created` carry `mode`, and `run.started` carries the `loop` settings.
- `loop.round_started` has `round`, the `ideas` going in, and the `participants`.
- `branch.created` fires for every idea, with `parent_ids` set to the idea it twisted (`br_seed` for an original), the last editor as the author, and its `round`.
- `loop.vote` has `round`, `idea_id`, `reason` and `ballot_size`.
- `loop.round_ended` has `tally`, `ballots`, `selected` (with each idea's `votes`), `random_fill` and `abstained`.
- `loop.retiring` has `round`, `agent_id` and `how` (`random` or `crash`). It's followed by the town's usual `agent.last_words`, `agent.died` (with `picked`), `replace.step` events and `agent.born`.
- `loop.replaced` has `round`, `died`, `born`, `how` and `fallback` (`null`, `design failed` or `design timed out`).
- `GET /runs/{id}` includes an `ideation` block with the current round, phase, participants and replacements.
