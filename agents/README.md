# Agent brain

Everything inside a character's head. Owned by Anthony.

| File | Does |
| --- | --- |
| `character.py` | The process the world starts with `python -m agents.character`. Joins the town, batches its inbox, takes turns, dies, designs replacements, notices crashes |
| `brain.py` | Prompts as named sections, the decision, reflection and last-words schemas, and the offline mock brain |
| `memory.py` | Memory stream, retrieval by recency and importance (top 8), the reflection trigger |
| `replace.py` | Propose, refine and content-check prompts and schemas for replacements |
| `llm.py` | Every LLM call. `big-pickle` through OpenCode Zen's OpenAI-compatible API (schema in the prompt, validated, one retry), Claude through the Anthropic SDK, or `mock` |
| `schema.py` | Persona and branch schemas, and the schema check |
| `messages.py` | Message types and body shapes |
| `config.py` | The plan's starting parameters, each overridable from the environment |
| `baseline.py` | One LLM's three ideas for the same seed |
| `../personas/starting_cast.json` | The six starting characters |

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
| `ANTHROPIC_API_KEY` | Only if a persona names a Claude model. Add the model to `ODDTOWN_MODELS` too |
| `ODDTOWN_TIME_SCALE` | Multiplies every wait and wall-clock lifespan. 0.1 makes a rehearsal 10 times faster |
| `ODDTOWN_MAX_ACTIONS`, `ODDTOWN_LIFESPAN_S`, `ODDTOWN_REST_AFTER_S` | Lifespan and stopping, for tuning |

Tests: `.venv/bin/python -m pytest agents/tests`. `test_town.py` runs the real observer, the real world service and six real characters on the mock model, through a death, a replacement and a crash.

## What a character emits to the observer

Everything goes through `observer.client`, so it passes `observer/events.py`.

| Type | Payload | When |
| --- | --- | --- |
| `agent.decision` | `action`, `say` (100 characters or fewer), `reason`, plus `target_branch`, `send_to`, `memory_ids`, `life_left` | Every turn |
| `branch.created` | `branch_id`, `parent_ids`, `op` (`twist` or `merge`), `authors`, `text` | A twist, or an accepted merge (emitted by whoever accepted) |
| `endorse`, `unendorse` | `branch_id`, `reason` | Backing or dropping a branch |
| `agent.last_words`, then `agent.died` | `text`, then `picked` | A death. After a crash, the peer that noticed emits `agent.died` for the dead agent, with `noticed_by` |
| `agent.reflection` | `insights`, `agenda` | After a reflection |
| `llm.call` | `kind`, `model`, `input_tokens`, `cache_read_tokens`, `output_tokens`, `latency_ms`, `retries`, `sections` (estimated input tokens per prompt section), `counts` | Every LLM call |
| `replace.step` | `step` (`propose`, `refine` or `birth`), `replaces`, `persona` | Each step of designing a replacement |
| Every message between characters | `to`, then the message body | `idea.share`, `idea.pitch`, `merge.propose`, replies, `objection`, `rest`, `agent.hello`, `replace.request`, `persona.draft` |
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
