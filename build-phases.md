# Observer and UI Build Phases

Oct 11, 2026 · Oleg

This is the build order for Oleg's lane, which is the observer and the UI described in [ui-plan.md](ui-plan.md). Each phase is small enough for Claude to finish and check in one go. Say "go phase N" to start one.

Every phase follows the same rules:

- It starts with `git pull`, so it builds on what Clara and Anthony have pushed.
- It ends with the app still running. Nothing is left half-built between phases.
- Claude runs every check in the "Done when" list itself, including looking at the page in the browser pane, and reports what passed and what didn't.
- It ends with one local commit. Claude only pushes when you say so, like "go phase 3 and push".

## Fixed choices

These hold for every phase, so no phase has to re-decide them.

| Thing | Choice |
| --- | --- |
| Observer | Python 3.12, FastAPI, SQLite from the standard library, one uvicorn worker on port 8100 |
| Mock world service | A separate small FastAPI app on port 8200 with the same endpoints Clara's world service will have, so switching to the real one only changes a URL |
| Python environment | `.venv` at the repo root: `uv venv --python 3.12 .venv`, then `uv pip install --python .venv/bin/python fastapi==0.143.0 'uvicorn[standard]==0.54.0' pydantic==2.14.0 pytest httpx2`. Tests run with `.venv/bin/python -m pytest observer` |
| UI | Vite, React and TypeScript on port 5173, with three.js through react-three-fiber and drei, and d3 for the tree |
| UI state | One reducer that turns events into town state, fed by one WebSocket. The reducer has unit tests |
| Models | The KayKit glTF files already in `ui/public/models/` |
| Starting it | Three terminals: `.venv/bin/uvicorn observer.app:app --port 8100`, `.venv/bin/uvicorn observer.mock_world:app --port 8200`, and `npm --prefix ui run dev`, then open http://localhost:5173. `.claude/launch.json` has the same three, but the desktop app couldn't start the Python ones because macOS blocked it from reading `.venv`, so start those from a terminal |

## Event contract

All events share one envelope, `{run_id, seq, ts, type, agent_id, payload}`. The observer assigns `seq`, so every run has one order. These are the events the UI uses:

| Type | Sent by | Payload | UI uses it for |
| --- | --- | --- | --- |
| `run.created` | World | `seed` | Starting the loading screen |
| `agent.born` | World or agents | `persona` (the full persona, traits included) | A building popping up, and a character card |
| `run.ready` | World | `agent_ids` | Showing the Start run button |
| `run.started` | World | none | Switching to the run view |
| `agent.decision` | Agents | `action`, `say` (100 characters or fewer), `reason` | The speech bubble shows `say` |
| `branch.created` | Agents | `branch_id`, `parent_ids`, `op`, `authors`, `text` | A node in the tree |
| `endorse`, `unendorse` | Agents | `branch_id` | Support counts in the tree |
| `world.event` | World | `kind` (seed, constraint or feedback), `text` | Shown later, not decided yet |
| `agent.last_words`, `agent.died` | Agents | `text`, then `picked` | Swapping a building |
| `run.ended` | World | `finalists`, `baseline` | The end screen, not decided yet |

The observer stores any other type too, like `idea.share` or `human.request`, and the UI ignores it for now.

The world service needs two endpoints for the Ask and Ready screens. `POST /runs` with `{seed}` returns `{run_id}`, brings the agents online and emits `run.created`, then one `agent.born` per agent, then `run.ready`. `POST /runs/{run_id}/start` broadcasts the seed and emits `run.started`.

## Phases

### Phase 0, contract

Turn the contract above into code so Clara and Anthony can build against it today.

Build:
- `observer/events.py` with pydantic models for the envelope and for each payload in the table, accepting unknown types.
- `observer/client.py` with `emit(...)` and `async aemit(...)`. Both use only the standard library, time out after one second and never raise, so a dead observer can't crash an agent.
- `ui/src/events.ts` with the same types in TypeScript.
- The Python environment, with pytest.

Done when:
- A test validates one sample of every event type, and rejects an envelope with a missing field.
- `emit` against a closed port returns within about a second without raising.

Needs from you: send `observer/events.py` and the world service endpoints to Clara and Anthony, and bring back any changes they want.

### Phase 1, observer

Build:
- `observer/store.py`, which appends events to SQLite (`observer.db`, gitignored), assigns `seq` per run and reads a run back in order.
- `observer/app.py` with `POST /events`, `GET /runs`, `GET /runs/{run_id}/events?after=seq` and a WebSocket at `/ws/{run_id}`. When the UI connects to the WebSocket it first gets every past event for that run, then new ones live. This makes live view and replay the same code path, and a page refresh mid-run just catches up.

Done when:
- Tests show that `seq` counts up per run, survives an observer restart, and that a WebSocket client connecting mid-run gets the backlog and then live events with no gaps or repeats.
- A real `emit` from a separate process shows up on a WebSocket client.

Needs from you: nothing.

### Phase 2, mock town

A fake town that plays a believable run through the real observer, so the UI never waits on the agents.

Build:
- `observer/mock_world.py` on port 8200 with the same two endpoints as the real world service.
- A scripted run that uses the six starting characters from the plan. The six births are about 0.7 seconds apart. After start, there's a decision with a `say` line every 2 to 4 seconds, plus branches, endorsements, one injected constraint, and one death and replacement at about one minute in. The run ends at about three minutes. A fixed random seed makes every mock run play out the same.

Done when:
- A test runs the mock run at 20 times speed and checks that the observer holds the right event sequence: `run.created`, 6 `agent.born`, `run.ready`, `run.started`, then the run, with every event valid against the contract.

Needs from you: nothing.

### Phase 3, UI shell and Ask screen

Build:
- The Vite app, the WebSocket connection, and the reducer from events to town state.
- The Ask screen, a single chat-style input reading "What idea would you like to test?" Submitting it calls `POST /runs` on the mock world and opens the WebSocket for the new run.
- A `.claude/launch.json` so Claude can start the observer, the mock world and the UI in the browser pane.

Done when:
- `npm run build` passes, and the reducer tests pass.
- In the browser pane, submitting an idea creates a run, and the app receives the six `agent.born` events and `run.ready`.

Needs from you: a look at the Ask screen's styling, if you care how it looks yet.

### Phase 4, city and loading

Build:
- The isometric city: an orthographic camera, ground and roads from KayKit, and six lots. Every run gets its own town, a compact block with rounded corners, seeded by the run id so a refresh shows the same town. Buildings always face the camera and front onto a road, and no building hides another. The six neighbors get six different buildings, shuffled per run.
- "Loading your city" text. Each `agent.born` pops its building up with a bounce and a burst of dust particles.

Done when:
- In the browser pane, all six buildings pop up one by one after an idea is submitted, and a screenshot shows the finished city.

Needs from you: a look at the screenshot, since this phase is mostly about how the city feels.

### Phase 5, Ready screen

Build:
- On `run.ready`, the Start run button appears, and below it one card per character with the name, one-liner and six trait bars.
- Clicking Start run calls `POST /runs/{run_id}/start`, and the page switches to the run view on `run.started`.

Done when:
- In the browser pane, the six cards show the right traits, and clicking Start run starts the mock run.

Needs from you: nothing.

### Phase 6, speech bubbles

Build:
- A speech bubble above a house on each `agent.decision`, showing `say`. Each house has at most one bubble, and a new one replaces the old. Bubbles fade after about 5 seconds.
- Until you decide on deaths, a death and replacement swaps the building using the same pop-up and dust animation.

Done when:
- In the browser pane, bubbles appear over the right houses during the mock run, they stay readable, and the death at one minute swaps a building.

Needs from you: a look at the bubbles.

### Phase 7, tab switcher and tree

Build:
- A floating City and Tree switcher, centered at the top, shown once the run starts.
- The tree view in d3, with nodes from `branch.created` labeled by op and author and sized or colored by living support. A merge has two parents, so the second parent is drawn as a dashed edge.

Done when:
- In the browser pane, switching tabs works mid-run without losing the city's state, and the tree grows as the mock run goes on.

Needs from you: nothing.

### Phase 8, real system

Point the UI at Clara's world service and the real agents instead of the mock, and fix whatever doesn't match the contract.

This is the one phase Claude can't finish alone, because it needs Clara's world service and Anthony's agents running. Start it once they have six agents gossiping, which is their hour-4 gate.

Done when: a real run plays through every screen from Ask to the tree.

## After phase 8

These are in [ui-plan.md](ui-plan.md) as not decided yet. Each can become its own phase once you decide what it looks like:

- What deaths and replacements look like in the city.
- Clicking a house.
- Faction flags and a billboard for world events.
- The inject panel and human request cards.
- The end screen with the finalists next to the single LLM's three ideas.
- Live reactions in the title. During the run, the title changes to the latest character's take on the idea, like "Tony thinks your plan is stupid" or "Lyla likes the idea but thinks it could be different". The simplest version builds the line from `agent.decision`, using the character's name and its action: back means it likes it, trash means it doesn't, and twist means it likes it but wants it different. If those lines feel too samey, ask Anthony to add a short `headline` field to `agent.decision`.
