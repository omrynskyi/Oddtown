"""A scripted fake run, so the UI can be built before the agents exist.

Pure and deterministic: the same seed text and random seed always give the same
steps. Each step is (seconds, type, agent_id, payload). `mock_world` plays them
through the real observer.

The cast here is a stand-in. The real starting cast is Anthony's
`personas/starting_cast.json`.
"""

import copy
import random
from dataclasses import dataclass

Step = tuple[float, str, str | None, dict]

RUN_LENGTH = 180.0
DEATH_AT = 60.0
CONSTRAINT_AT = 90.0
CONSTRAINT = "It has to work with no electricity"


@dataclass
class Character:
    persona: dict
    twists: list[str]  # branch text, {seed} is filled in
    says: list[str]  # speech bubble lines, 100 characters or fewer
    last_words: str


def persona(id, name, one_liner, worldview, red_line, voice, loves, hates, traits):
    axes = ("time_horizon", "tech_attitude", "scale", "sociality", "risk", "rationality")
    return {
        "id": id,
        "name": name,
        "one_liner": one_liner,
        "worldview": worldview,
        "loves": loves,
        "hates": hates,
        "red_line": red_line,
        "voice": voice,
        "traits": dict(zip(axes, traits)),
        "model": "mock",
        "lineage": {"generation": 0, "designed_by": [], "replaces": None},
    }


CAST = [
    Character(
        persona("char_marg", "Marguerite Bell", "Retired lighthouse keeper who distrusts anything that needs electricity",
                "Things worth having should outlast you.", "Never backs an idea that needs an app",
                "Short sentences, nautical metaphors, dry humor", ["objects that last fifty years", "the sea"],
                ["subscriptions", "screens"], (0.9, -0.8, -0.6, -0.4, 0.2, -0.3)),
        ["{seed}, delivered once a year in an oak chest that outlives you",
         "{seed} you collect by rowing out to a lighthouse",
         "{seed} with no app, no account, just a ledger and a key"],
        ["A monthly box is a leash.", "Build it to last fifty years or don't build it.",
         "Needs a battery? Then it's already broken.", "Steady now. Good ideas don't rush.",
         "That one sinks in the first storm.", "I'd back it if it had no screen."],
        "Keep the chest. Lose the app. The sea will outlast all of you.",
    ),
    Character(
        persona("char_dez", "Dez Moreno", "Competitive pigeon racer", "Every problem is really a logistics problem.",
                "Trashes any idea with no delivery plan", "Fast, clipped, talks in routes and times",
                ["route maps", "punctual birds"], ["vague plans", "missed handoffs"], (0.1, 0.3, 0.2, 0.4, 0.5, 0.8)),
        ["{seed} on a fixed route, same doorstep, same minute, every week",
         "{seed} handed off at railway stations along one line",
         "{seed} where neighbors relay each other's deliveries"],
        ["Cool idea. How does it get to the door?", "No route, no deal.",
         "Cut a stop and it's twice as fast.", "I'll carry that one. It has a plan.",
         "Who's on the last mile? Nobody? Trash.", "Same doorstep, same minute. That's trust."],
        "Somebody take the last mile. I never got to finish the route.",
    ),
    Character(
        persona("char_anselm", "Brother Anselm", "A monk who thinks in decades", "Anything worth doing takes ten years.",
                "Won't back anything built for speed", "Slow, gentle, fond of parables",
                ["patience", "bells"], ["deadlines", "growth hacks"], (1.0, -0.5, -0.3, -0.7, -0.8, 0.2)),
        ["{seed} you join for ten years, with a vow instead of a contract",
         "{seed} that only ships at harvest, once a season",
         "{seed} tended by one keeper per village for a lifetime"],
        ["Why must it arrive this week?", "Ten years from now, will anyone remember it?",
         "Slow it down and it becomes worth waiting for.", "A vow lasts longer than a contract.",
         "I will sit with this one a while.", "Speed is the enemy of meaning."],
        "Do not hurry the next one. Let them grow slowly.",
    ),
    Character(
        persona("char_priya", "Priya Raman", "Hyper-growth startup founder", "If it can't reach a billion people it's a hobby.",
                "Won't back anything that can't scale", "Energetic, metric-heavy, says 'unlock' a lot",
                ["flywheels", "network effects"], ["artisanal anything", "small markets"], (-0.6, 0.9, 1.0, 0.6, 0.9, 0.5)),
        ["{seed} as a platform where anyone can be a supplier",
         "{seed} with a referral loop that pays you in product",
         "{seed} licensed to every office building on Earth"],
        ["Cute. Does it scale to a billion?", "This is a feature, not a company.",
         "Add a referral loop and it grows itself.", "I'm backing the one with network effects.",
         "Ship it Friday, fix it Monday.", "Artisanal is a polite word for small."],
        "Whoever comes next: think bigger than I did.",
    ),
    Character(
        persona("char_tamsin", "Tamsin Hale", "Theater kid turned funeral director", "Every product is secretly a ritual.",
                "Rejects ideas with no ceremony in them", "Dramatic, warm, speaks in stage directions",
                ["candles", "exits and entrances"], ["forgettable moments", "fine print"], (0.4, -0.2, -0.2, 0.8, 0.3, -0.7)),
        ["{seed} where every delivery is opened together at sunset",
         "{seed} with a farewell ceremony when you cancel",
         "{seed} that marks each birthday with a rare blend"],
        ["Where's the ceremony? Where's the moment?", "Lights down. Box opens. Everyone gasps.",
         "Even cancelling should feel like a farewell.", "I'll back anything with a candle in it.",
         "This needs an entrance.", "No ritual, no memory, no point."],
        "Exit, stage left. Make the next one's entrance unforgettable.",
    ),
    Character(
        persona("char_rook", "Rook Adeyemi", "Deep-sea biologist", "Everything should work like an ecosystem.",
                "Trashes anything that creates waste", "Calm, precise, borrows from marine biology",
                ["closed loops", "symbiosis"], ["packaging", "single-use anything"], (0.7, 0.1, 0.5, -0.3, -0.2, 0.9)),
        ["{seed} where the grounds feed a neighborhood garden that grows the next batch",
         "{seed} in returnable jars that cycle like a reef",
         "{seed} where every customer is also a supplier"],
        ["Where does the waste go? Follow it.", "Close the loop and it feeds itself.",
         "Nature already solved this. Look at a reef.", "Single-use? Then it's trash already.",
         "I'll back the one that returns what it takes.", "Symbiosis beats competition here."],
        "Return what you take. That is all an ecosystem asks.",
    ),
]

NEWCOMER = Character(
    persona("char_ines", "Ines Okafor", "Night-shift air traffic controller who trusts only checklists",
            "Nothing is safe until it has been checked twice.", "Won't back anything without a fallback",
            "Calm, procedural, counts things out loud", ["checklists", "quiet radios"], ["improvising", "single points of failure"],
            (0.3, 0.4, 0.3, -0.1, -0.9, 1.0)),
    ["{seed} with a backup supplier for every single step",
     "{seed} that runs on a printed checklist anyone can follow"],
    ["Checklist first. Then we talk.", "What's the fallback if that fails?",
     "Confirmed. That one has a backup.", "Two independent suppliers or it's not safe.",
     "Hold. Somebody verify that.", "Clear to proceed."],
    "",
)

BASELINE = [
    "{seed} with personalized picks chosen by AI",
    "{seed} with a loyalty program and member rewards",
    "{seed} for offices, billed per team",
]

OBJECTIONS = [
    "Nobody has said how it gets to the door.",
    "It needs an app, and half the town won't touch it.",
    "It can't grow past one street.",
    "It throws away more than it delivers.",
    "There is no moment in it anyone would remember.",
]

# What characters say to each other, by how the other one answers. Lines are 200 characters or fewer.
NOTES = [
    "Have a look at this one. It might surprise you.",
    "You of all people should hear this.",
    "I think this is the one worth backing.",
    "Tell me where this goes wrong.",
]
YES = ["Yes. Finally, someone gets it.", "Fine, I'm in.", "That works. I'll back it.", "You've talked me round."]
NO = ["No. That's not what I'd build.", "Absolutely not.", "You lost me at the second line.", "That sinks in the first storm."]
COUNTER = ["Close. Here's my version.", "Only if we add a twist of my own."]

ACTIONS = ["twist", "back", "pitch", "trash", "merge", "ignore"]
WEIGHTS = [35, 25, 12, 10, 10, 8]


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def prepare_timeline(seed: str) -> list[Step]:
    """From run.created to run.ready: the town comes online one building at a time."""
    steps: list[Step] = [(0.0, "run.created", None, {"seed": seed})]
    for i, character in enumerate(CAST):
        steps.append((0.5 + 0.7 * i, "agent.born", character.persona["id"], {"persona": character.persona}))
    ready_at = steps[-1][0] + 0.5
    steps.append((ready_at, "run.ready", None, {"agent_ids": [c.persona["id"] for c in CAST]}))
    return steps


def run_timeline(seed: str, rng_seed: int = 7) -> list[Step]:
    """From run.started to run.ended: gossip, one constraint, one death and replacement."""
    rng = random.Random(rng_seed)
    # Messages between characters get their own random stream, so adding them left the run itself unchanged.
    talk = random.Random(f"{rng_seed}-talk")
    steps: list[Step] = [
        (0.0, "run.started", None, {}),
        (0.2, "world.event", None, {"kind": "seed", "text": seed}),
        (0.4, "branch.created", None, {"branch_id": "br_000", "parent_ids": [], "op": "seed", "authors": ["world"], "text": seed}),
        (CONSTRAINT_AT, "world.event", None, {"kind": "constraint", "text": CONSTRAINT}),
    ]
    living = {c.persona["id"]: c for c in CAST}
    branches = [{"id": "br_000", "authors": ["world"], "text": seed}]
    endorsed: dict[str, list[str]] = {id: [] for id in living}
    used_twists: dict[str, set[str]] = {}
    newcomer: tuple[float, Character] | None = None
    died = False

    def new_branch(t, agent, parent_ids, op, authors, text):
        branch_id = f"br_{len(branches):03d}"
        branches.append({"id": branch_id, "authors": authors, "text": text})
        steps.append((t + 0.1, "branch.created", agent,
                      {"branch_id": branch_id, "parent_ids": parent_ids, "op": op, "authors": authors, "text": text}))
        return branch_id

    def back(t, agent, branch_id):
        if branch_id in endorsed[agent]:
            return
        if len(endorsed[agent]) == 2:
            dropped = endorsed[agent].pop(0)
            steps.append((t + 0.15, "unendorse", agent, {"branch_id": dropped}))
        endorsed[agent].append(branch_id)
        steps.append((t + 0.2, "endorse", agent, {"branch_id": branch_id}))

    def converse(t, agent, action, branch_id):
        """A message to a peer and its reply, a second or so later. Both are from characters still alive."""
        peers = sorted(a for a in living if a != agent)
        # The replies must land before the one death, so no one speaks in the last moments before it.
        if not peers or (not died and t >= DEATH_AT - 1.7):
            return
        peer, note = talk.choice(peers), talk.choice(NOTES)
        yes = talk.random() < (0.3 if action == "trash" else 0.6)
        say = talk.choice(YES if yes else NO)
        if action == "merge":
            send = ("merge.propose", {"to": peer, "branch_ids": [branch_id], "note": note})
            answer = "accept" if yes else talk.choice(["reject", "counter"])
            if answer == "counter":
                say = talk.choice(COUNTER)
            reply = ("merge.response", {"to": agent, "answer": answer, "text": say})
        elif action == "pitch":
            send = ("idea.pitch", {"to": peer, "branch_id": branch_id, "note": note})
            reply = ("pitch.response", {"to": agent, "branch_id": branch_id, "persuaded": yes, "text": say})
        else:
            send = ("idea.share", {"to": peer, "branch_id": branch_id, "note": note})
            reply = ("idea.reaction", {"to": agent, "branch_id": branch_id, "answer": "accept" if yes else "reject", "text": say})
        steps.append((t + 0.3, send[0], agent, send[1]))
        steps.append((t + 1.6, reply[0], peer, reply[1]))

    t = 1.0
    while True:
        previous, t = t, t + rng.uniform(2, 4)
        if t >= RUN_LENGTH - 1:
            break

        if not died and t >= DEATH_AT:
            died = True
            # Let the previous decision's endorsements land before anyone dies.
            death_t = max(DEATH_AT, previous + 0.5)
            dying = rng.choice(sorted(living))
            character = living.pop(dying)
            picked = rng.sample(sorted(living), 3)
            steps.append((death_t, "agent.last_words", dying, {"text": character.last_words}))
            steps.append((death_t + 0.5, "agent.died", dying, {"picked": picked}))
            born = copy.deepcopy(NEWCOMER)
            born.persona["lineage"] = {"generation": 1, "designed_by": picked, "replaces": dying}
            newcomer = (death_t + 4.0, born)
            steps.append((death_t + 4.0, "agent.born", born.persona["id"], {"persona": born.persona}))
            steps.append((death_t + 4.3, "agent.hello", born.persona["id"], {"text": born.says[0]}))

        if newcomer and t >= newcomer[0]:
            living[newcomer[1].persona["id"]] = newcomer[1]
            endorsed[newcomer[1].persona["id"]] = []
            newcomer = None

        agent = rng.choice(sorted(living))
        character = living[agent]
        action = rng.choices(ACTIONS, WEIGHTS)[0]
        others = [b for b in branches if b["id"] != "br_000"]
        if action == "merge" and len(others) < 2:
            action = "twist"
        fresh = [tw for tw in character.twists if tw not in used_twists.get(agent, set())]
        if action == "twist" and not fresh:
            action = "back" if others else "ignore"
        if action in ("back", "pitch", "trash") and not others:
            action = "twist"

        decision = {"action": action, "say": clip(rng.choice(character.says), 100), "reason": character.persona["worldview"]}
        steps.append((t, "agent.decision", agent, decision))

        if action == "twist":
            parent = rng.choice(branches)
            twist = rng.choice(fresh)
            used_twists.setdefault(agent, set()).add(twist)
            text = twist.format(seed=seed)
            branch_id = new_branch(t, agent, [parent["id"]], "twist", [agent], text)
            decision["target_branch"] = parent["id"]
            if rng.random() < 0.5:
                back(t, agent, branch_id)
            converse(t, agent, action, branch_id)
        elif action == "merge":
            a, b = rng.sample(others, 2)
            authors = list(dict.fromkeys([agent, *b["authors"]]))
            text = f"{a['text']}, combined with {b['text'][0].lower()}{b['text'][1:]}"
            new_branch(t, agent, [a["id"], b["id"]], "merge", authors, text)
            decision["target_branch"] = a["id"]
            converse(t, agent, action, a["id"])
        elif action == "back":
            target = rng.choice(others)
            decision["target_branch"] = target["id"]
            back(t, agent, target["id"])
            converse(t, agent, action, target["id"])
        elif action in ("pitch", "trash"):
            decision["target_branch"] = rng.choice(others)["id"]
            converse(t, agent, action, decision["target_branch"])

    support = {b["id"]: [a for a in living if b["id"] in endorsed[a]] for b in branches if b["id"] != "br_000"}
    ranked = sorted(support, key=lambda id: (-len(support[id]), id))[:3]
    text = {b["id"]: b["text"] for b in branches}
    finalists = [
        {"branch_id": id, "text": text[id], "champions": support[id], "minority_report": rng.choice(OBJECTIONS)}
        for id in ranked
    ]
    baseline = [idea.format(seed=seed) for idea in BASELINE]
    steps.append((RUN_LENGTH, "run.ended", None, {"finalists": finalists, "baseline": baseline}))
    return sorted(steps, key=lambda step: step[0])


def full_run(run_id: str, seed: str, rng_seed: int = 7, start_ts: float = 1760173200.0) -> list[dict]:
    """A whole mock run as stored events, with seq and ts, as the UI receives them."""
    prepare = prepare_timeline(seed)
    ready_at = prepare[-1][0] + 2.0
    steps = prepare + [(ready_at + t, *rest) for t, *rest in run_timeline(seed, rng_seed)]
    return [
        {"run_id": run_id, "seq": seq, "ts": start_ts + t, "type": type, "agent_id": agent_id, "payload": payload}
        for seq, (t, type, agent_id, payload) in enumerate(steps, start=1)
    ]


if __name__ == "__main__":
    # Writes the UI's test fixture: python -m observer.mock_script > ui/src/fixtures/mock-run.json
    import json

    print(json.dumps(full_run("run_fixture", "a coffee subscription"), indent=1))
