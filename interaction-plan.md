# Interaction Feedback and Group Patterns Plan

Oct 11, 2026 · Oleg

This plan adds two things to the observer and UI lane in [build-phases.md](build-phases.md). First, the city shows when two characters message each other and whether they agree. Second, an analyser reads what they say and finds groups that form and how they disagree. Both sit downstream of the event stream, so neither touches the town.

## What exists already

Every point-to-point message between characters is posted to the observer by the sender, with `agent_id` set to the sender and `payload.to` set to the recipient ([agents/character.py](agents/character.py), `send`). Replies carry an outcome the sender already decided, so nothing new is needed from Clara or Anthony:

| Message | Reply | Reply field | Agree | Disagree | Mixed |
| --- | --- | --- | --- | --- | --- |
| `idea.share` | `idea.reaction` | `answer` | `accept` | `reject` | none |
| `idea.pitch` | `pitch.response` | `persuaded` | `true` | `false` | none |
| `merge.propose` | `merge.response` | `answer` | `accept` | `reject` | `counter` |

Replies also carry `text`, up to 200 characters, which is the character's own reason. `endorse`, `unendorse` and `objection` are broadcasts, so they are not pairwise.

## Rules for every phase

- Same rules as [build-phases.md](build-phases.md): `git pull` first, end with the app running, run every check and report, one local commit, push only when told.
- The observer and everything built here never send anything back to the town. Agents never read the observer.
- Anything an LLM produces is stored as an event, so replay shows the same result with no API calls.
- An LLM failure never blocks the event feed. The UI falls back to what it already knows.

## Phase 9, letters between houses (built)

No LLM. The outcome fields above are enough.

What it does:
- Each message (`idea.share`, `idea.pitch`, `merge.propose`) sends an SVG envelope flying from the sender's roof to the recipient's. When it lands, the flap swings open and the envelope unfolds into a card showing the message's `note`.
- Each reply sends an envelope back the same way, and its card carries a stamp and a colored edge: green "Yes!" for agree, red "Boooo!" with a tomato for disagree, amber "Hmm…" for a counter.
- The card is the old speech bubble's shape without the tail, so there is one look for everything a house says. A character's own line from `agent.decision` and its last words use the same cards, since they have no letter. Each house stacks up to three cards, newest at the bottom.
- `ui/src/town.ts` turns the six message types into `interactions`, keeping the latest 8 and ignoring anyone who isn't living in town. `ui/src/city/Letter.tsx` is the flying envelope and `ui/src/city/HouseCards.tsx` is the stack.
- The mock run (`observer/mock_script.py`) now has characters messaging each other and answering, on its own random stream so the rest of the run is unchanged.

Checked: reducer tests for every row of the table, a mock-run test that every message gets its reply, and a look in the browser pane at the flight, the unfold and a stacked house.

## Phase 10, Claude stance check

This is the "claudy" layer. The outcome field is binary and the text can disagree with it, for example an `accept` that reads grudging. Claude reads the pair and gives a finer stance.

Build:
- `observer/stance.py`. On each reply, it makes one call through `call_json` from [agents/llm.py](agents/llm.py), with the original message and the reply, returning `stance` (`agree`, `disagree` or `mixed`) and a `reason` of 12 words or fewer.
- The call runs in the background after the reply is stored, then posts `observer.stance` back to the observer with the sender, the recipient and the `seq` of the reply it judged. It uses the model from `ODDTOWN_MODEL`, so `mock` makes it copy the structured outcome with no network.
- `observer/events.py` gets a payload model for `observer.stance`.
- The UI recolours the card's edge and stamp when the stance arrives, and swaps the stamp for the stance reason.

Done when:
- Tests show a reply with mismatched answer and text gets the stance from the text, that `mock` mode copies the outcome, and that a failing call leaves the card as phase 9 drew it.
- Replaying a stored run shows the same colours with no model calls.

Needs from you: whether the reasons read well enough to keep.

## Phase 11, group patterns

Build:
- Layer one, no LLM: an interaction tally per pair of characters, built from stances and from the public endorsements, then clusters of characters who mostly agree with each other and disagree with others. It also tracks characters who switch sides.
- Layer two, LLM: every ~20 events and at run end, a call gets the clusters plus the quotes behind them and returns a list of patterns. Each has a `label` for the axis of disagreement, like "cheap vs premium", its `members`, and the `seq` numbers of its evidence. Patterns without evidence are dropped.
- The result is posted as `observer.insight` events.
- The UI shows insights as cards, with each card's evidence jumping to that moment. Where the cards sit is not decided yet.

Patterns to look for: rival blocs and what splits them, unlikely allies who back the same branch for different reasons, characters who flip sides, a character everyone trashes, and a lone dissenter nobody answers.

Done when:
- Tests show the clustering on a hand-built set of stances, and that a pattern with no evidence is dropped.
- A mock run shows at least one insight card with working evidence links.

Needs from you: how insights should look, and whether they appear live or only at the end.

## Open questions

- The `objection` message has not been checked for its payload. If it names a target, it can be a red letter too.
- Whether the cards are enough, or the reasons should also appear somewhere persistent, like a log.
- Phase 10 uses the town's own model, which may be a free hosted or local one. If its stance calls are poor, switch that one call to a Claude model.
