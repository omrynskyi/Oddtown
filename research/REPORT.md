# Can a peer-to-peer agent town encourage creative ideas?

Oct 11, 2026 · agent engineering study on the Oddtown character brain

## The short answer

Yes, but not by telling agents to be creative. The original town had a strong cast and produced vivid ideas, but each character mostly argued for its own idea, kept re-pitching people who had already said no, and rarely built on anyone else's work. Its replacements also collapsed into copies of whoever designed them. Nothing in the prompts was wrong. The problem was what the characters could remember and what each message let them do.

We added memory and communication mechanics instead of behavior instructions: a per-peer record of what actually passed between two characters, retrieval that brings back what a turn is about, replies that can carry your own version of the other's idea, credit when someone builds on your work, answers that wake you so exchanges continue, thinking before acting, and replacement design that is checked against the town. None of them tells a character to cooperate, befriend or argue.

With the same cast, seed, model and time budget, the new brain:

| | Original | New | |
| --- | --- | --- | --- |
| Twists that build on someone else's idea | 22% | 54% | 6 characters, coffee |
| | 0% | 53% | 6 characters, library |
| | 26% | 32% | 12 characters, coffee |
| Re-pitching someone who already said no (per message) | 0.24 | 0.03 | 6, coffee |
| | 0.10 | 0.08 | 12, coffee |
| Pitches as a share of all actions | 34% | 29% | 6, coffee |
| | 46% | 19% | 12, coffee |
| Merges as a share of all actions | 6% | 17% | 12, coffee |
| Replies that accept | 23% | 31% | 6, coffee |
| | 19% | 38% | 12, coffee |
| Pairs with both a yes and a no between them (negotiation) | 0 | 4 | 12, coffee |
| Pairs that only ever said no to each other | 18 | 6 | 12, coffee |
| Characters woven into one idea's ancestry (mean) | 1.49 | 1.89 | 6, coffee |
| Newcomer persona overlap with an earlier character (shared words) | 18.7 | 3.1 | coffee, 6 vs 12 |

The cost is speed. Thinking first makes every decision about a third slower on the local model, so the new town takes fewer turns in the same 12 minutes.

## Setup

- **Model:** `qwen3.6:35b` on a local Ollama, thinking off, serving one call at a time. Every character runs on it.
- **Town:** the real platform (world service, one process per character, HTTP transport, observer). Lifespans of 6 minutes, staggered, so every run has deaths and replacements. Each run is 12 minutes.
- **Casts:** the six starting characters, and twelve with six more radical ones (`personas/extended_cast.json`): an accelerationist, a prepper, a con-artist magician, a permits clerk who loves paperwork, a nine-year-old inventor, and a tenants' union organizer.
- **Seeds:** "a coffee subscription", "a public library", "a dating app".
- **Conditions:** `ODDTOWN_ABLATE=all` reproduces the original brain exactly. No flag is the new brain. Single mechanisms can be switched off for ablations.
- **Measures:** `research/metrics.py` computes everything from the observer's events, with no model calls. `research/judge.py` adds two model-judged measures, grounding and idea quality. `research/replay.py` reruns logged prompts under prompt variants.

## What the original town did

From two earlier runs and a fresh baseline:

- **Agents forgot each other.** Retrieval kept the top 8 memories by recency and importance. A rejection from three turns ago fell out of view, so 41% of messages in one run re-pitched an idea to someone who had already turned it down. Rook pitched his vent temple to Marguerite six times.
- **There was no dialogue.** A reply didn't wake the sender, so every exchange was one message and one verdict.
- **Advocacy beat creation.** Pitching was the most common action. Replies were binary, and 76 to 94% were rejections.
- **Ideas stayed with their authors.** Only 6 to 22% of twists built on someone else's branch. The average idea's ancestry held 1.1 to 1.5 characters.
- **Replacements collapsed.** Designers wrote newcomers in their own image. One run's newcomers were "Elara Vance", "Elara Voss" and "Silas Vane", all cold-archive types. Another was reborn as an exact "Dez Moreno", plus two "Kovic" growth hackers cloned from Priya.
- **The town could go extinct.** A designer that died with a design request still queued never did it. In one pilot run every character died and nobody was born.

## What we changed, and why

The paper's agents work because of three things: a memory stream retrieved by recency, importance and relevance, reflection that turns memories into beliefs, and dialogue conditioned on what each agent remembers about the other. Oddtown had the first two halves of the first one. Every change below adds a mechanism and leaves behavior to the agent.

| Mechanism | What it does | Where |
| --- | --- | --- |
| Relationship ledger | Code writes one line per exchange between two characters, from what actually happened, plus tallies: ideas taken up and turned down each way, who built on whose work. The prompt's People section shows the full record for the peers a turn is about and two lines for everyone else | `agents/social.py`, `brain.people_section` |
| Relevance retrieval | Every memory records which characters and branches it's about. Retrieval adds the paper's relevance term: overlap with the peers and branches in the inbox and the agenda | `agents/memory.py` |
| Threads | Answers, and someone building on your idea, wake you, so an exchange can go back and forth | `character.answered` |
| Ideas in replies | Every reply has a verdict and an optional `idea`, your own version their message sparked. It becomes a branch built on theirs and goes back with the answer. A no with an idea is a counter-proposal | `brain.Reply`, `character.reply` |
| Credit | Twisting someone else's branch tells its authors, and branches show whose work they build on | `character.credit` |
| Grounded reflection | Insights cite memory ids and are dropped if they cite nothing real. Reflection writes a one-line impression of each peer into the ledger | `character.reflect` |
| Think first | `reason` is the first field the model writes, before it picks an action | `brain.Decision` |
| Rules rewrite | The rules describe mechanics and drop the adversarial framing. One line says what you say about others must match the record | `brain.RULES` |
| Replacement design | Designers see every name used and the emptiest corner of trait space. They write three contrasting concepts before choosing one, and five candidate names, of which code takes the first that's free. Code checks each draft against everyone who has lived and tells the next designer who it reads like | `agents/replace.py` |
| Robustness | A dying character finishes design work it owes. The last one alive designs its own successor | `character.die` |

## Prompt experiments

`research/replay.py` reran 26 real decision prompts from the baseline run four ways, holding the situation fixed:

| Variant | Actions | Replies that said yes | Twists off the seed |
| --- | --- | --- | --- |
| Original rules, reason last | pitch 18, twist 5 | 14% | 4 of 5 |
| Original rules, reason first | pitch 20, twist 5 | 24% | 3 of 5 |
| New rules, reason last | pitch 14, twist 9 | 7% | 8 of 9 |
| New rules, reason first | pitch 14, twist 7, merge 2 | 21% | 7 of 7 |

- **Thinking first roughly doubled openness** under both rule sets. A model that commits to `action` before it writes `reason` rejects by reflex. Written first, the reasoning engages the other idea. Priya, given Tamsin's funeral-ritual box: "By combining my viral mechanics with her ritual container, we create a scalable product that sells the transition."
- **The new rules moved effort from advocacy to making things.** There were more twists and fewer pitches, and nearly every twist built on an existing branch.
- **A separate "build" answer failed.** It was used once in 29 replies. The model read it as endorsement: "violates my red line, I cannot build on it." Splitting the verdict from the optional idea fixed the framing, so a character can say no and still offer its version.
- **Making `idea` a required, nullable field** didn't raise its use in these snapshots, because they come from a town with no ledger and old "win them over" agendas. In live runs, ideas in replies do appear, and they are real syntheses. Marguerite's sealed tin plus Rook's worms became "spore-encased grounds that reactivate in garden soil."

## Full runs

All runs are 12 minutes, one per cell. "Per decision" and "per message" rates make runs with different turn counts comparable.

| Run | Characters | Decisions | Pitch | Twist | Merge | Built on others | Mixing | Accept | Nagging per message | Allies | Rivals |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Original, coffee, 6 | 12 | 67 | 34% | 49% | 10% | 22% | 1.49 | 23% | 0.24 | 4 | 9 |
| New (pilot), coffee, 6 | 12 | 49 | 29% | 43% | 8% | 54% | 1.89 | 31% | 0.03 | 1 | 4 |
| Original, coffee, 12 | 20 | 71 | 46% | 44% | 6% | 26% | 1.37 | 19% | 0.10 | 2 | 6 |
| New, coffee, 12 | 18 | 47 | 19% | 53% | 17% | 32% | 1.34 | 38% | 0.08 | 2 | 1 |
| New, library, 6 | 10 | 58 | 14% | 62% | 7% | 53% | 1.76 | 27% | 0.10 | 3 | 5 |

*Pending: the original on the library seed (the first attempt lost the model halfway), the dating-app seed, the four ablations and a clean rerun of the new 6-character coffee town. The pilot ran before three bug fixes described below.*

### What it looks like

**An alliance across worldviews, and an argument that absorbs the other side** (12 characters, coffee). Folasade, the tenants' organizer, proposes a "tin-refill co-op" to Marguerite, the lighthouse keeper who hates subscriptions. Marguerite accepts: "If the drinkers hold the tiller, the tin won't rust in a warehouse somewhere." Nadia, the permits clerk, offers a merge that is all paperwork. Folasade turns it down ("Paperwork is the skeleton, Nadia, but I won't let it hide the meat") and then writes her next version with Nadia's Form 12-B in it. Her last words credit both: "br_de8cab merges your durability, Nadia's admin, and our assembly votes."

**Remembering an objection and coming back with a fix.** Nadia rejects Dez's pigeon courier for GPS privacy under "Section 4". Two turns later Dez writes: "Nadia, I've removed the GPS to comply with Section 4." She still objects, now on livestock licensing. The rivalry continues, on new ground.

**Changing course because of a specific person.** Brother Anselm, after Marguerite turned down his oak-aged beans: "Marguerite rejected my barrel-aged idea because wood rots… I approach permanence through iron." He sends her a hand-forged iron carafe that improves with every decade of use.

**Building a mechanism together.** Three newcomers (Elias, Kaelen, Elara) spent their lives on one idea, a living vessel that digests coffee grounds. Over eight turns they argued about whether the vessel is a filter or an engine. Each correction made it more specific ("the grounds are substrate, not biomass: you need acidophages to digest the lignin"), and two merges came out of it.

**Impressions that track the record.** From reflections in the library run, Marguerite on Anselm: "A steady hand on the tiller, but he hasn't picked up an oar himself yet." Kaelen on Elara, over two reflections: "Butcher of the scream. Her ledger is noise", then "Her ledger is noise, but her greed provides the necessary friction for erasure."

### Convergence: the new brain stopped committing

The town still ends the same way. When it settles or time runs out, the world service takes the up to three branches with the most living backers as finalists. But the first version of the new brain almost stopped endorsing anything:

| Run | Endorsements | Finalists (living support) |
| --- | --- | --- |
| Original, coffee, 6 | 23 | 3, 2, 1 |
| New, coffee, 6 | 2 | 1 |
| Original, coffee, 12 | 15 | 2, 1, 1 |
| New, coffee, 12 | 3 | none |
| New, library, 6 | 10 | 2, 1, 1 |

The characters explored and built, but didn't commit. Nothing in the prompt said that backers decide the town's answer, and with reasoning first they spent their turns making things. No run in either condition ended by the town coming to rest, so at 12 minutes convergence was already weak.

The fix is information, not an instruction to endorse. The rules now state how the town's answer is chosen, and every turn shows the current standing ("The town's answer right now, by living backers: br_x (3 backers), …"). *Measured in the rerun of the new brain, pending.*

## More than six characters

Twelve characters made the town more social and less deep.

- **Every character got fewer turns.** One local model serves one call at a time, so twelve characters share the throughput six had. In 12 minutes there were 47 to 71 decisions, so 3 to 6 per character. Decisions waited 52 seconds on average, and reflections 76.
- **The original brain turned hostile at scale.** At 12 characters, pitches rose to 46% of actions, acceptance fell to 19%, and of the pairs who talked, none ever had both a yes and a no between them. 18 pairs only ever rejected each other.
- **The new brain held up.** Merges rose to 17% of actions and acceptance to 38%. Four pairs negotiated (both yes and no) and six were purely hostile. The extra characters were used: co-op plus bylaws, equity flywheel plus drone swarm, a nine-year-old adding "slime and leveling up" to a compost subscription.
- **Ideas didn't have time to travel.** Cross-building was 32% at 12 characters against 54% at 6, and mixing stayed at 1.3. With 3 to 4 turns per life, a character rarely lives long enough to build on a branch that was built on another. More characters need more model throughput or longer lives, not a different brain.

## Bugs found on the way

| Problem | Effect | Fix |
| --- | --- | --- |
| A designer that died with a design request queued dropped it | Towns went extinct | Dying characters finish their design work. The last one designs its own successor |
| Designers wrote newcomers in their own image | Monocultures of vent ecologists or growth hackers | Three concepts, then choose. Code checks text overlap with everyone who has lived |
| The model's favorite names (Elara, Voss, Vane, Kaelen, Thorne) | Two Elaras in one town confused everyone | Five candidate names, and code takes the first free one |
| Reflection evidence cited as `[obs_6]` | Every insight was dropped | Ids are normalized and checked against the whole memory stream |
| Building on the seed sent credit to the world service | Wasted messages | Credit only goes to characters |
| A reply's idea repeated as the turn's twist | Duplicate branches | A twist that repeats a reply's branch shares that branch instead |
| New ideas written only into pitch notes | Ideas that never became branches | The rules say an idea travels only as a branch |

## Limitations

- **One run per cell, one model.** The effects on nagging, cross-building, pitching and acceptance are large and point the same way across seeds and town sizes, but small differences, like novelty, are noise.
- **Lexical metrics.** Novelty and spread are TF-IDF distances, which reward poetic wording. The model-judged idea scores are pending.
- **A single local model serving every character** limits turns per character, especially at 12. A hosted model with parallel calls would let lifetimes be longer and make the 12-character results more meaningful.

## What to try next

- **Give the twelve-character town the throughput it needs**, through parallel model calls or longer lives, and measure whether mixing rises past two characters per idea.
- **Hand newcomers the town's social knowledge.** Designers know the town. Passing their impressions to the newcomer would let it start with relationships instead of strangers.
- **Make novelty social.** When a twist reads like an existing branch, tell its author which one, the way replacement drafts are checked now.
