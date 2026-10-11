# Oddtown 3-Person Split

Oct 10, 2026

This is the three-person version of the build plan in [oddtown-plan-and-architecture.md](oddtown-plan-and-architecture.md). The original plan has four lanes. Here the Evidence and pitch lane is split across the other three, so every person owns one side of the system plus part of the evidence.

The three people only depend on each other through five small contracts that get agreed in the first 30 minutes. After that, each person can build and test alone against stubs until the integration gates.

## Who owns what

| Person | Lane | Owns | Folders |
| --- | --- | --- | --- |
| 1 | Platform and experiments | Everything that moves messages or starts processes, plus the headless experiment harness | `transport/`, `registry/`, `world/`, `experiments/run_conditions.py`, `experiments/metrics.py`, `docker-compose.yml` |
| 2 | Agent brain | Everything inside a character's head, plus the baselines that reuse its prompts | `agents/`, `personas/`, `experiments/baselines.py` |
| 3 | Observer, UI and pitch | Everything downstream of the event stream, plus the charts, ratings form, slides and submission | `observer/`, `ui/` |

In short, Person 1 builds the pipes, Person 2 builds the minds, and Person 3 builds the window everyone sees them through.

## Hour 0 kickoff, all three, 30 minutes

Settle the open questions first:

- Which LLM and embedding model to use, and whether they run hosted or locally. The API key goes in `.env`.
- Plain asyncio or LangGraph. We recommend plain asyncio because it's faster to write.
- Whether a judge picks the seed live or we stick to a rehearsed one.

Then freeze these contracts as stub files. Nobody writes feature code until they are committed.

| Contract | File | Owner | Used by |
| --- | --- | --- | --- |
| Transport and registry interface: `send`, `broadcast`, `register`, `search`, `remove` | `transport/adapter.py`, `registry/adapter.py` | 1 | 2 |
| Message envelope and types from the plan's message protocol | `agents/messages.py` | 2 | 1 |
| Persona and branch schemas | `agents/schema.py` | 2 | 1, 3 |
| Observer event schema and an `emit(event)` client | `observer/events.py` | 3 | 1, 2 |
| Condition flags: memory on or off, replacement mode, lifespan on or off | `agents/config.py` | 2 | 1 |

Observer events use the shape `{run_id, seq, ts, type, agent_id, payload}`. The types are every protocol message plus `agent.decision` (action, reason and retrieved memory ids), `agent.reflection`, `agent.born`, `agent.died`, `branch.created` (with the branch embedding), `human.request`, `human.answer` and `run.ended` (finalists).

In the first 15 minutes Person 1 commits the repo skeleton from the plan's layout, so everyone works in the same tree.

## Person 1, platform and experiments

Person 1's goal is that Person 2 is never blocked on AGNTCY, so the fallbacks get built first and the real integration second.

| Hours | Task | Done when |
| --- | --- | --- |
| 0 to 0.75 | Repo skeleton, adapter interfaces, an in-memory registry and a local transport using asyncio queues or HTTP | Person 2 can run two characters with no Docker |
| 0.75 to 1.5 | `docker-compose.yml` with SLIM on port 46357 and the Directory on localhost:8888, plus a hello-world A2A exchange over SLIM | **Gate 1.5.** Two agents exchange a message, and one OASF record is searchable |
| 1.5 to 3 | `slim_a2a.py` and `agntcy_dir.py` behind the adapters. A bounced send raises a dead-peer error | Switching `TRANSPORT=slim` or `REGISTRY=agntcy` needs no change to agent code |
| 3 to 4.5 | World service in FastAPI with endpoints to start a run, inject a seed or constraint, and answer a human request, plus the 15 minute and token budget stops. A launcher that starts one process per character | **Gate 4**, shared with Person 2. Six processes gossip a seed |
| 4.5 to 6 | Newcomer processes spawned on birth, Directory publish on birth and delete on death, and the crash path: kill a process by hand and a peer notices the bounce | **Gate 6**, shared with Person 2 |
| 6 to 8 | `metrics.py` for diversity, distance from seed, diversity over time and reinventions. Finalist selection with minority reports. `run_conditions.py` for the full town and A1 to A3 | One headless 5 minute run writes a JSONL log and a row in the metrics CSV |
| 8 to 10 | Run the condition grid (3 seeds by 3 random seeds) in parallel in the background. Hardening and fallback switches. Record the locked-seed demo run with Person 3 | **Gate 10.** The numbers are in and the recording is saved |
| 10 to 12 | Set up the demo machine, rehearse the live run, write the architecture section of the submission | |

Person 1 owns two fallbacks:

- If SLIM isn't working by hour 1.5, switch the app-sdk to NATS, and if that also fails, use plain A2A over HTTP.
- If the Directory isn't working by hour 3, keep the in-memory registry.

## Person 2, agent brain

Person 2's goal is a character that makes one good decision per LLM call, and that dies and gets replaced cleanly.

| Hours | Task | Done when |
| --- | --- | --- |
| 0 to 1.5 | Persona and branch schemas, schema and content checks, the six starting characters in `personas/starting_cast.json`, and `agents/llm.py` with chat, JSON mode and embeddings | **Gate 1.5.** All personas validate, and one decision call returns valid JSON |
| 1.5 to 4 | Memory stream, importance scores returned by the decision call, retrieval by recency, importance and relevance (top 8), the decision call, the character loop that runs on each message or on a 5 to 15 second idle timer, the six actions, the two-endorsement limit, and an `emit` call for every act. Built on Person 1's local transport | **Gate 4.** Six characters gossip a seed end to end |
| 4 to 6 | Lifespan counted in actions and wall-clock time with staggered starts, last words, picking three survivors, and `replace.py` running propose, refine, refine and birth. The newcomer sends its hello | **Gate 6.** An agent dies, and a newcomer joins the gossip |
| 6 to 7 | `baselines.py` for B1 to B3, reusing the persona prompts. Condition flags for A1 to A3 handed to Person 1 | Person 1's runner can run every condition |
| 7 to 8.5 | Reflection and agenda, `rest` and the stopping rule, `human.request` for rule breaks and feasibility disputes, and freezing a branch until the human answers | |
| 8.5 to 10 | Prompt tuning on real runs, covering how novel the replacements are, drift away from the seed, and the starting parameters | **Gate 10.** Feature freeze |
| 10 to 12 | Write the implementation details section and help rehearse | |

Person 2 owns two fallbacks:

- If LLM calls are slow, cap concurrency and use a smaller model for decisions.
- If replacements all look alike, raise the proposer's temperature and seed it with a random job, era and obsession.

## Person 3, observer, UI and pitch

Person 3's goal is that judges can see every beat of the demo script on screen, either live or from a replay.

| Hours | Task | Done when |
| --- | --- | --- |
| 0 to 1.5 | The event schema and `emit` client, the observer (FastAPI, append-only SQLite, WebSocket feed and replay by run id), and a mock event generator that plays a fake run | **Gate 1.5.** The UI scaffold shows the mock feed over WebSocket |
| 1.5 to 4.5 | The idea tree in d3, with branches laid out by parent and labeled by op and author, the message feed, and factions colored by living endorsements | A mock run can be followed from start to finish |
| 4.5 to 7 | The idea map and persona map as 2D projections of branch embeddings and trait vectors, the character family tree with lineage, deaths and newcomers, and a character drawer that shows its memories and the reason behind each act | Every view works on a real event log |
| 7 to 8.5 | The inject panel and human request cards, both calling Person 1's world service, and the run-end view with finalists and minority reports | **Gate 8.** The UI draws a live run |
| 8.5 to 10 | The diversity-over-time chart and condition comparison from Person 1's CSV, a shuffled and unlabeled human ratings form, and recording the demo with Person 1 | **Gate 10** |
| 10 to 12 | Collect ratings from 10 to 15 people, build the slides, and assemble and submit | **Gate 12** |

Person 3 owns one fallback. If live integration slips past hour 8, the demo runs from a replay of the recorded JSONL log.

## Handoffs

| By hour | From | To | What |
| --- | --- | --- | --- |
| 0.75 | 1 | 2 | Local transport and in-memory registry |
| 1 | 3 | 1, 2 | The `emit` client and the observer's URL |
| 1.5 | 2 | 1, 3 | `llm.py` with embeddings, and the starting cast JSON |
| 3 | 1 | 2 | Real SLIM and Directory adapters behind the same interface |
| 3 | 2 | 3 | The first real event log, from a two-agent run |
| 4.5 | 1 | 3 | World service endpoints for start, inject and human answer |
| 7 | 2 | 1 | Condition flags and `baselines.py` |
| 8 | 1 | 3 | The metrics CSV and the finalists output |
| 9.5 | 1 and 3 | Everyone | The locked-seed recorded run |

At hours 4, 6 and 8 all three people stop for 15 minutes to integrate on one machine. If a gate is missed by more than 30 minutes, the lane that owns it switches to its fallback.

## If we fall behind

Nobody starts Identity badges or the earned-lifespan modes unless they are ahead of schedule. If we still have to cut, we cut in this order:

1. AGNTCY Identity badges. Nobody owns these unless they are ahead.
2. Earned-lifespan modes, owned by Person 2. We keep fixed lifespans only.
3. The A2 random-replacement ablation, owned by Persons 1 and 2.
4. Polish on the persona map, owned by Person 3.
5. The A1 memory ablation, owned by Persons 1 and 2.

We never cut gossip, branches, lifespans, replacements, the B1 baseline or replay.
