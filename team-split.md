# Oddtown 3-Person Split

Oct 10, 2026

This is the three-person version of the build plan in [oddtown-plan-and-architecture.md](oddtown-plan-and-architecture.md). Every person owns one side of the system, and the pitch is shared at the end.

The three people only depend on each other through four small contracts that get agreed in the first 30 minutes. After that, each person can build and test alone against stubs until the integration gates.

## Who owns what

| Person | Lane | Owns | Folders |
| --- | --- | --- | --- |
| 1, Clara | Platform | Everything that moves messages or starts processes | `transport/`, `registry/`, `world/`, `docker-compose.yml` |
| 2, Anthony | Agent brain | Everything inside a character's head | `agents/`, `personas/` |
| 3, Oleg | Observer, UI and pitch | Everything downstream of the event stream, plus the slides and submission | `observer/`, `ui/` |

In short, Person 1 builds the pipes, Person 2 builds the minds, and Person 3 builds the window everyone sees them through.

## Hour 0 kickoff, all three, 30 minutes

Already decided: plain asyncio, a rehearsed seed, one model per character, and no embeddings anywhere.

Settle the last open question first: which models go on which characters, and whether they run hosted or locally. API keys go in `.env`.

Then freeze these contracts as stub files. Nobody writes feature code until they are committed.

| Contract | File | Owner | Used by |
| --- | --- | --- | --- |
| Transport and registry interface: `send`, `broadcast`, `register`, `search`, `remove` | `transport/adapter.py`, `registry/adapter.py` | 1 | 2 |
| Message envelope and types from the plan's message protocol | `agents/messages.py` | 2 | 1 |
| Persona and branch schemas | `agents/schema.py` | 2 | 1, 3 |
| Observer event schema and an `emit(event)` client | `observer/events.py` | 3 | 1, 2 |

Observer events use the shape `{run_id, seq, ts, type, agent_id, payload}`. The types are every protocol message plus `agent.decision` (action, a short reason for the speech bubble, and retrieved memory ids), `agent.reflection`, `agent.born`, `agent.died`, `branch.created`, `human.request`, `human.answer` and `run.ended` (finalists).

In the first 15 minutes Person 1 commits the repo skeleton from the plan's layout, so everyone works in the same tree.

## Person 1, Clara, platform

Person 1's goal is that Person 2 is never blocked on AGNTCY, so the fallbacks get built first and the real integration second.

| Hours | Task | Done when |
| --- | --- | --- |
| 0 to 0.75 | Repo skeleton, adapter interfaces, an in-memory registry and a local transport using asyncio queues or HTTP | Person 2 can run two characters with no Docker |
| 0.75 to 1.5 | `docker-compose.yml` with SLIM on port 46357 and the Directory on localhost:8888, plus a hello-world A2A exchange over SLIM | **Gate 1.5.** Two agents exchange a message, and one OASF record is searchable |
| 1.5 to 3 | `slim_a2a.py` and `agntcy_dir.py` behind the adapters. A bounced send raises a dead-peer error | Switching `TRANSPORT=slim` or `REGISTRY=agntcy` needs no change to agent code |
| 3 to 4.5 | World service in FastAPI with endpoints to start a run, inject a seed or constraint, and answer a human request, plus the 15 minute and token budget stops. A launcher that starts one process per character | **Gate 4**, shared with Person 2. Six processes gossip a seed |
| 4.5 to 6 | Newcomer processes spawned on birth, Directory publish on birth and delete on death, and the crash path: kill a process by hand and a peer notices the bounce | **Gate 6**, shared with Person 2 |
| 6 to 8 | Run end in the world service: once a majority of agents post `rest` or the budget runs out, tally living support and write the top three branches with their minority reports into `run.ended` | A full run ends cleanly with three finalists |
| 8 to 10 | Hardening and fallback switches, then record the locked-seed demo run with Person 3 | **Gate 10.** The recording is saved |
| 10 to 12 | Set up the demo machine, rehearse the live run, write the architecture section of the submission | |

Person 1 owns two fallbacks:

- If SLIM isn't working by hour 1.5, switch the app-sdk to NATS, and if that also fails, use plain A2A over HTTP.
- If the Directory isn't working by hour 3, keep the in-memory registry.

## Person 2, Anthony, agent brain

Person 2's goal is a character that makes one good decision per LLM call, and that dies and gets replaced cleanly.

| Hours | Task | Done when |
| --- | --- | --- |
| 0 to 1.5 | Persona and branch schemas, schema and content checks, the six starting characters in `personas/starting_cast.json`, and `agents/llm.py` with chat and JSON mode, using whichever model the persona names | **Gate 1.5.** All personas validate, and one decision call returns valid JSON |
| 1.5 to 4 | Memory stream, importance scores returned by the decision call, retrieval by recency and importance (top 8), the decision call, the batched inbox (wake on shares, pitches, merge offers and world events, wait 3 seconds, answer everything in one call) with a 20 to 40 second idle timer, the six actions, the two-endorsement limit, and an `emit` call for every act. Built on Person 1's local transport | **Gate 4.** Six characters gossip a seed end to end |
| 4 to 6 | Lifespan counted in actions and wall-clock time with staggered starts, last words, picking three survivors, and `replace.py` running propose, refine, refine and birth. The newcomer sends its hello | **Gate 6.** An agent dies, and a newcomer joins the gossip |
| 6 to 8.5 | `agents/baseline.py`, one call that gives the same seed and constraints to one model and returns three ideas. Reflection and agenda, `rest` and the stopping rule, `human.request` for rule breaks and feasibility disputes, and freezing a branch until the human answers | |
| 8.5 to 10 | Prompt tuning on real runs, covering how novel the replacements are, drift away from the seed, and the starting parameters | **Gate 10.** Feature freeze |
| 10 to 12 | Write the implementation details section and help rehearse | |

Person 2 owns two fallbacks:

- If LLM calls are slow, cap concurrency and move slow characters to a faster model.
- If replacements all look alike, raise the proposer's temperature and seed it with a random job, era and obsession.

## Person 3, Oleg, observer, UI and pitch

Person 3's goal is that judges can see every beat of the demo script on screen, either live or from a replay.

| Hours | Task | Done when |
| --- | --- | --- |
| 0 to 1.5 | The event schema and `emit` client, the observer (FastAPI, append-only SQLite, WebSocket feed and replay by run id), a mock event generator that plays a fake run, and a Vite, React and react-three-fiber scaffold with an isometric camera | **Gate 1.5.** The mock feed reaches the browser over WebSocket |
| 1.5 to 4.5 | The city view: six lots with KayKit buildings, one speech bubble per house for each decision, faction flags from endorsements, and world events on a billboard | A mock run reads clearly in the city |
| 4.5 to 6 | Deaths and births, where the last words show, the building sinks and a new one rises on the same lot. Clicking a house shows the character's persona and recent thoughts | The city works on a real event log |
| 6 to 7.5 | The idea tree view in d3, with branches labeled by op and author, colored by living support, and merges drawn with two parents | |
| 7.5 to 8.5 | The inject panel and human request cards, both calling Person 1's world service, and the run-end view with finalists and minority reports next to the baseline's three ideas | **Gate 8.** The UI draws a live run |
| 8.5 to 10 | Polish, and recording the demo with Person 1 | **Gate 10** |
| 10 to 12 | Build the slides, and assemble and submit | **Gate 12** |

Person 3 owns one fallback. If live integration slips past hour 8, the demo replays the recorded run from the observer's SQLite database.

## Handoffs

| By hour | From | To | What |
| --- | --- | --- | --- |
| 0.75 | 1 | 2 | Local transport and in-memory registry |
| 1 | 3 | 1, 2 | The `emit` client and the observer's URL |
| 1.5 | 2 | 1, 3 | `llm.py` and the starting cast JSON |
| 3 | 1 | 2 | Real SLIM and Directory adapters behind the same interface |
| 3 | 2 | 3 | The first real event log, from a two-agent run |
| 4.5 | 1 | 3 | World service endpoints for start, inject and human answer |
| 8 | 1 | 3 | The `run.ended` event with finalists |
| 9.5 | 1 and 3 | Everyone | The locked-seed recorded run |

At hours 4, 6 and 8 all three people stop for 15 minutes to integrate on one machine. If a gate is missed by more than 30 minutes, the lane that owns it switches to its fallback.

## If we fall behind

If we have to cut, we cut in this order:

1. Polish on the city view, like cars driving between houses, owned by Person 3.
2. Reflection and agenda, owned by Person 2. Memory and retrieval stay.

We never cut gossip, branches, lifespans, replacements or replay.
