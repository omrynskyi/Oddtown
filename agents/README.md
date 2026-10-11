# Agent brain

Everything inside a character's head. Owned by Anthony.

| File | Does |
| --- | --- |
| `character.py` | The process the world starts with `python -m agents.character`. Joins the town, batches its inbox, takes turns, dies, designs replacements, notices crashes |
| `brain.py` | Prompts as named sections, the decision, reflection and last-words schemas, and the offline mock brain |
| `memory.py` | Memory stream, retrieval by recency, importance and relevance to who and what the turn is about (top 8), the reflection trigger |
| `social.py` | The relationship ledger: what passed between this character and each peer, tallies, their ideas, and its impression of them |
| `replace.py` | Propose, refine and content-check prompts and schemas for replacements |
| `llm.py` | Every LLM call. `big-pickle` through OpenCode Zen's OpenAI-compatible API (schema in the prompt, validated, one retry), Claude through the Anthropic SDK, or `mock` |
| `schema.py` | Persona and branch schemas, and the schema check |
| `messages.py` | Message types and body shapes |
| `config.py` | The plan's starting parameters, each overridable from the environment |
| `ideation.py` | The agent for the ideation voting loop (`ODDTOWN_MODE=ideation_loop`): writes, twists and votes. See `PLATFORM.md` |
| `baseline.py` | One LLM's three ideas for the same seed |
| `../personas/starting_cast.json` | The six starting characters. `extended_cast.json` adds six more, for twelve |

## Run it

Characters inherit the world service's environment, so export `.env` in the shell that starts it.

```bash
set -a && source .env && set +a
.venv/bin/uvicorn observer.app:app --port 8100
.venv/bin/python -m world.service --port 8200
```

| Setting | Use |
| --- | --- |
| `ODDTOWN_MODEL=mock` | Runs the whole town with no API key. Remove it to use each persona's `model`, which is `big-pickle` |
| `OPENCODE_API_KEY` | Key for OpenCode Zen, where `big-pickle` runs. It's free for now, and OpenCode may use the data to improve the model |
| `ODDTOWN_LLM_BASE_URL`, `ODDTOWN_REASONING_EFFORT` | Point at another OpenAI-compatible server, like a local Ollama at `http://127.0.0.1:11434/v1`. Set the effort to `none` for thinking models such as `qwen3.6:35b`, which otherwise think for 25+ seconds a call. Ollama ignores the key, but `OPENCODE_API_KEY` must still be set to something |
| `ANTHROPIC_API_KEY` | Only if a persona names a Claude model. Add the model to `ODDTOWN_MODELS` too |
| `ODDTOWN_TIME_SCALE` | Multiplies every wait and wall-clock lifespan. 0.1 makes a rehearsal 10 times faster |
| `ODDTOWN_MAX_ACTIONS`, `ODDTOWN_LIFESPAN_S`, `ODDTOWN_REST_AFTER_S` | Lifespan and stopping, for tuning |
| `ODDTOWN_ABLATE` | Mechanisms to switch off, for experiments. See below |
| `ODDTOWN_PROMPT_LOG` | A folder. Every prompt and answer is appended there, one JSONL file per character |

Tests: `.venv/bin/python -m pytest agents/tests`. `test_town.py` runs the real observer, the real world service and six real characters on the mock model, through a death, a replacement and a crash.

## How characters deal with each other

The prompts don't tell characters to cooperate, befriend or argue. These mechanisms give them what they need to, and what they do with it is up to them. Each can be switched off with `ODDTOWN_ABLATE` (comma-separated, or `all` for the original brain), which is how `research/` measures them.

| Mechanism | What it does |
| --- | --- |
| `social` | A ledger per peer, written by code from what actually happened: every exchange in one line, who took up or turned down whose ideas, who built on whose work, and the character's own impression. The prompt's People section shows the full record for the peers a turn is about, and two lines for the rest, so what characters say about each other comes from the record |
| `relevance` | Memories record who and which branches they're about. Retrieval adds the paper's relevance term, scoring memories by overlap with the peers and branches in the inbox and the agenda |
| `threads` | An answer to your message, or someone building on your idea, wakes you, so an exchange can go back and forth instead of ending at the first reply |
| `build` | Every reply can carry `idea`, the character's own version that the message sparked. It becomes a branch built on theirs and goes back with the answer. A reject with an idea is a counter-proposal |
| `credit` | Twisting someone else's branch tells its authors (`idea.built`), and branches show whose work they build on |
| `reflect` | Reflection insights cite memory ids and are dropped if they cite nothing real. Reflection also writes a one-line impression of each peer into the ledger |
| `design` | Replacement designers see every name used so far and the emptiest corner of trait space, computed by code |
| `rules` | The rewritten rules, which describe the mechanics without telling characters how to behave |
| `think` | `reason` comes first in the decision schema, so the model thinks before it picks an action |
| `quorum` | A branch backed by 2 living characters besides its own authors is a proposal. Proposals are listed first and tagged `[proposal]`, and the town's standing names them |
| `compost` | If nobody likes a branch, it fades. Only its author hears every answer to it, so the author tracks who it showed the branch to. Once all of them turned it down and nobody living backs it, the author broadcasts `branch.withdraw` and everyone composts it. A dying character withdraws its unbacked branches too, unless a co-author is still alive. A composted branch drops out of the branch list and newcomers' briefings, but stays in the record, still shows when it's in someone's inbox, and comes back if anyone backs it |
| `budget` | Each character keeps at most 3 unbacked branches of its own. A new one past that withdraws the oldest, and everyone composts it. The prompt shows where the character stands |

A dying character finishes any design work it was handed first, and the last character alive designs its own successor, so the town can't go extinct.

## What a character emits to the observer

Everything goes through `observer.client`, so it passes `observer/events.py`.

| Type | Payload | When |
| --- | --- | --- |
| `agent.decision` | `action`, `say` (100 characters or fewer), `reason`, plus `target_branch`, `send_to`, `memory_ids`, `life_left` | Every turn |
| `branch.created` | `branch_id`, `parent_ids`, `op` (`twist` or `merge`), `authors`, `text` | A twist, or an accepted merge (emitted by whoever accepted) |
| `endorse`, `unendorse` | `branch_id`, `reason` | Backing or dropping a branch |
| `branch.withdraw` | `branch_id`, `why` (`unliked`, `budget` or `died`) | Retiring an unbacked branch of its own: everyone it was shown to turned it down, it was over the branch budget, or its author died |
| `agent.last_words`, then `agent.died` | `text`, then `picked` | A death. After a crash, the peer that noticed emits `agent.died` for the dead agent, with `noticed_by` |
| `agent.reflection` | `insights`, `agenda`, `impressions` (`peer_id`, `impression`) | After a reflection |
| `llm.call` | `kind`, `model`, `input_tokens`, `cache_read_tokens`, `output_tokens`, `latency_ms`, `retries`, `sections` (estimated input tokens per prompt section), `counts` | Every LLM call |
| `replace.step` | `step` (`propose`, `refine` or `birth`), `replaces`, `persona` | Each step of designing a replacement |
| Every message between characters | `to`, then the message body | `idea.share`, `idea.pitch`, `merge.propose`, replies (`idea.reaction` and `pitch.response` carry `branch` when the reply built on the idea), `idea.built`, `objection`, `rest`, `agent.hello`, `replace.request`, `persona.draft` |
| `llm.error`, `agent.error`, `agent.bounced` | | Something went wrong, or a send bounced |

`agent.born` comes from the world service once the newcomer joins. A newcomer's persona carries a `briefing` (seed, constraints, branches in play), because it missed the seed broadcast.

## What a character sends to the world service

| Type | Body |
| --- | --- |
| `usage.report` | `tokens` (all input plus output), and the breakdown, after every LLM call |
| `spawn.request` | `persona`, with its `briefing` |
| `human.request` | `kind` `rule_break` or `feasibility` with `branch_id` and `claim`, or `kind` `persona_review` with `persona` and `flagged` when a newcomer fails the content check |

## Still open

- **Clara:** `run.ended` has `baseline: []`. `await agents.baseline.run_baseline(seed, constraints)` returns the three ideas. Starting it at `/start` and awaiting it at the end hides its latency.
- **Clara:** the `human.answer` broadcast carries `request_id`, but the character that sent the request never learns the id the world assigned. So for now a persona review takes the oldest pending answer. Replying to `human.request` with its `request_id` would fix it.
- **Clara:** when the world's own broadcast notices a crash first, it deletes the record, and no character does the replacement pick.
- **Oleg:** `agent.died` after a crash has the real `picked` list plus `noticed_by`, rather than an empty list.
