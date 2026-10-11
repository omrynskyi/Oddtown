"""What a character thinks: prompts, output schemas and the offline mock brain.

Prompts are built as named sections (see agents.llm.Section), stable first:

    system: rules (same for every character), persona (same for this character's life)
    user:   world, town, branches, memories, inbox, to_score, task

Each branch's text appears once, in the branches section. Memories and inbox
lines refer to branches by id, so the same text isn't paid for several times.

The output schemas leave out numeric and length limits on purpose. The code
clamps and clips after parsing, so a score of 11 or a long speech bubble never
costs a retry.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, create_model

from agents import config
from agents.llm import Section
from agents.memory import Observation
from agents.social import Ledger
from agents.messages import IDEA_PITCH, IDEA_SHARE, MERGE_PROPOSE

RULES_V1 = """You are one character in Oddtown, a town of AI characters with clashing worldviews who argue over how to twist one seed idea into something more creative. Ideas spread only by gossip between characters. Nobody runs the town and nobody owns the idea. When a character dies, three survivors design its replacement, so nobody holds the idea forever.

Each turn you take exactly one action:
- twist: rewrite `target_branch` into a new version through your worldview. Put the new version in `text`, and say who to share it with in `send_to`.
- merge: offer to combine `target_branch` with `other_branch`, written as one idea in `text`. It goes to `send_to` (usually the other branch's author) and only becomes a branch if they accept.
- back: endorse `target_branch`. You can back at most 2 branches at a time, and backing a third drops your oldest. Endorsements are public.
- trash: publicly object to `target_branch`, with the objection in `reason`. It can become the minority report against that branch.
- pitch: try to win `send_to` over to `target_branch`, with your pitch in `note`.
- ignore: do nothing this turn.

Rules:
- Your loves, hates and red line come first. Never back a branch that crosses your red line.
- Stay on the seed idea and on every constraint the world has added.
- Be specific and surprising. A good twist changes how the idea works, not just how it looks.
- Branch text is one or two sentences, under 200 characters.
- `say` is your speech bubble about this act: under 100 characters, in your own voice.
- `reason` is one short line explaining your act.
- `note` is a short message to `send_to`, in your voice.
- Reply to every message under "Messages waiting for your reply", using its msg_id. accept means you like the idea, are persuaded, or accept the merge. reject means you don't. counter is only for merges, and means you accept with your own merged wording in `text`.
- Rate every observation under "New observations to rate" from 1 to 10 by how much it matters to you.
- Use `endorse` and `unendorse` for any extra changes to what you back. Leave them empty if nothing changes.
- Set `flag_human` only when a branch breaks a hard rule of the seed (law, physics, budget) or two sides dispute a fact that can't be settled by argument. Otherwise leave it null.
- Use only branch ids and character ids that appear in this prompt. Leave fields that don't apply to your action null."""

RULES = """You are one character in Oddtown, a town of AI characters, each with a strong worldview of their own, reshaping one seed idea. Ideas travel only person to person: you know a branch only if someone showed it to you. Nobody runs the town and nobody owns the idea. Characters die, and survivors design their replacements. When the town settles or time runs out, the branches with the most living backers become the town's answer, and the strongest public objection to each becomes its minority report.

Each turn you take exactly one action:
- twist: write a new version of `target_branch`, any branch you know, in `text`, and show it to `send_to`.
- merge: offer `send_to` one idea combining `target_branch` and `other_branch`, written in `text`. It becomes a branch only if they accept.
- back: publicly endorse `target_branch`, with why in `note`. You back at most 2 at a time, and a third drops your oldest.
- trash: publicly object to `target_branch`, with the objection in `note`.
- pitch: make the case for an existing `target_branch` to `send_to`, in `note`.
- ignore: do nothing this turn.
An idea of your own only travels as a branch, so a new idea goes in a twist (or a reply's `idea`), never only in a note.

Answer every message under "Messages waiting for your reply", by its msg_id:
- `answer`: accept if you like it, are persuaded, or take the merge as written; reject if you don't; counter, for merges only, to take the merge with your own wording in `idea`.
- `text`: what you say back to them, in your voice.
- `idea`: optional, whatever your answer. If their idea sparks a version of your own, write it here. It becomes a branch built on theirs, credited to you, and they see it with your answer. A reject with an idea is a counter-proposal.

Writing:
- `reason` is your private thinking for this turn, written first, in two to four sentences.
- Branch text is one or two concrete sentences, under 200 characters. A new version changes how the idea works, not just its wording.
- `say` is your speech bubble, under 100 characters, in your voice. `note` is your message to `send_to`.
- Never back a branch that crosses your red line. Stay on the seed idea and every constraint the world added.
- What you say about other characters and past conversations must match what's under "People" and in your memories.
- Rate every observation under "New since your last turn" from 1 to 10 by how much it matters to you.
- Use `endorse` and `unendorse` for any extra changes to what you back. Leave them empty if nothing changes.
- Set `flag_human` only when a branch breaks a hard rule of the seed (law, physics, budget) or two sides dispute a fact that argument can't settle.
- Use only branch ids and character ids that appear in this prompt. Leave fields that don't apply null."""


# -- output schemas -----------------------------------------------------------


class Reply(BaseModel):
    msg_id: str
    answer: Literal["accept", "reject", "counter"]
    text: str = Field(description="What you say back to them")
    idea: str | None = Field(description="Your own version their idea sparked, or your merge wording for counter. Null if none")


class ReplyV1(BaseModel):
    msg_id: str
    answer: Literal["accept", "reject", "counter"]
    text: str = Field(description="Your reason, or your merged wording when you counter")

    @property
    def idea(self) -> str | None:
        return self.text if self.answer == "counter" else None


class Score(BaseModel):
    obs_id: str
    importance: int = Field(description="1 to 10")


class HumanFlag(BaseModel):
    kind: Literal["rule_break", "feasibility"]
    branch_id: str
    claim: str


class Decision(BaseModel):
    reason: str = Field(description="Your private thinking for this turn, before you decide, in two to four sentences")
    replies: list[Reply]
    importance: list[Score]
    action: Literal["twist", "merge", "back", "trash", "pitch", "ignore"]
    target_branch: str | None
    other_branch: str | None = Field(description="The second branch, for merge only")
    text: str | None = Field(description="New branch text, for twist and merge")
    send_to: str | None = Field(description="A character id, for twist, merge and pitch")
    note: str | None
    endorse: list[str]
    unendorse: list[str]
    flag_human: HumanFlag | None
    say: str = Field(description="Speech bubble, under 100 characters")


def decision_model() -> type[BaseModel]:
    """The decision schema for the switched-on mechanisms. Field order is the order the model writes them in."""
    if config.on("think") and config.on("build"):
        return Decision
    fields = {name: (f.annotation, f) for name, f in Decision.model_fields.items()}
    if not config.on("build"):
        fields["replies"] = (list[ReplyV1], Decision.model_fields["replies"])
    if not config.on("think"):
        fields = {k: v for k, v in fields.items() if k != "reason"} | {"reason": (str, Field())}
        fields = {k: fields[k] for k in [*[k for k in fields if k not in ("reason", "say")], "reason", "say"]}
    return create_model("Decision", **fields)


class Insight(BaseModel):
    text: str
    evidence: list[str] = Field(description="Ids of the memories it rests on")


class Impression(BaseModel):
    peer_id: str
    impression: str = Field(description="One line: what you make of them now")


class Reflection(BaseModel):
    insights: list[Insight] = Field(description="Two or three insights about the ideas and the other characters")
    impressions: list[Impression] = Field(description="One for each character you've dealt with")
    agenda: str = Field(description="The idea you want to push next, and whose help or opposition matters for it, one or two sentences")


class ReflectionV1(BaseModel):
    insights: list[str] = Field(description="Two or three insights about the ideas and the other characters")
    agenda: str = Field(description="The branch you champion and who you want to win over, one or two sentences")


def reflection_model() -> type[BaseModel]:
    return Reflection if config.on("reflect") else ReflectionV1


class LastWords(BaseModel):
    text: str = Field(description="Under 280 characters")


# -- what the character can see -----------------------------------------------


@dataclass
class View:
    """Everything one decision call can see. Built fresh by the character for every turn."""

    me: dict
    seed: str | None
    constraints: list[str]
    life_left: int
    life_max: int
    seconds_left: float
    agenda: str
    my_endorsed: list[str]
    peers: list[dict]  # living personas, not including me
    endorsements: dict[str, list[str]]  # agent id -> branch ids it backs
    branches: dict[str, dict]  # branch id -> branch
    memories: list[Observation] = field(default_factory=list)
    inbox: list[dict] = field(default_factory=list)  # waking envelopes that need a reply
    to_score: list[Observation] = field(default_factory=list)
    idle: bool = False
    ledger: Ledger | None = None  # what passed between you and each peer
    focus: set[str] = field(default_factory=set)  # peers this turn is about, shown with their full history

    def name(self, agent_id: str | None) -> str:
        if agent_id == self.me["id"]:
            return "you"
        for p in self.peers:
            if p["id"] == agent_id:
                return p["name"]
        return agent_id or "someone"

    def backers(self, branch_id: str) -> list[str]:
        living = {p["id"] for p in self.peers} | {self.me["id"]}
        return [a for a, ids in self.endorsements.items() if branch_id in ids and a in living]


def clip(text: str | None, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# -- prompt sections ----------------------------------------------------------


def persona_text(p: dict) -> str:
    return (
        f"You are {p['name']}, {p['one_liner']}. Your id is {p['id']}.\n"
        f"Worldview: {p['worldview']}\n"
        f"Loves: {', '.join(p['loves'])}\n"
        f"Hates: {', '.join(p['hates'])}\n"
        f"Red line: {p['red_line']}\n"
        f"Voice: {p['voice']}"
    )


def system_sections(me: dict) -> list[Section]:
    return [Section("rules", RULES if config.on("rules") else RULES_V1), Section("persona", persona_text(me))]


def world_section(v: View) -> Section:
    lines = [f"Seed idea: {v.seed or '(none yet)'}"]
    if v.constraints:
        lines.append("Constraints from the world: " + " | ".join(v.constraints))
    lines.append(f"Life left: {v.life_left} of {v.life_max} actions, about {round(v.seconds_left)} seconds.")
    if v.life_left <= 1:
        lines.append("This is your last action before you die.")
    if v.agenda:
        lines.append(f"Your agenda: {v.agenda}")
    lines.append("You back: " + (", ".join(v.my_endorsed) if v.my_endorsed else "nothing yet"))
    if config.on("rules"):
        support = sorted(((len(v.backers(b)), b) for b in v.branches if v.backers(b)), reverse=True)[:3]
        standing = ", ".join(f"{b} ({n} {'backer' if n == 1 else 'backers'})" for n, b in support)
        lines.append("The town's answer right now, by living backers: " + (standing or "nothing is backed yet"))
    return Section("world", "\n".join(lines))


def town_section(v: View) -> Section:
    lines = ["Living characters:"]
    for p in v.peers:
        backs = ", ".join(v.endorsements.get(p["id"], [])) or "nothing"
        lines.append(f"- {p['id']} {p['name']}: {p['one_liner']}. Backs {backs}.")
    if not v.peers:
        lines.append("- nobody else")
    return Section("town", "\n".join(lines))


FULL_HISTORY = 6  # exchanges shown for the peers a turn is about
SHORT_HISTORY = 2  # for everyone else
CLOSE_PEERS = 2  # peers with the most history also get it in full


def people_section(v: View) -> Section:
    """Every living peer, with the record of what passed between you. Replaces the town section."""
    ledger = v.ledger or Ledger()
    close = sorted(v.peers, key=lambda p: -ledger.closeness(p["id"]))[:CLOSE_PEERS]
    full = v.focus | {p["id"] for p in close if ledger.closeness(p["id"])}
    lines = ["People in town (what's recorded here is what actually happened):"]
    for p in v.peers:
        backs = ", ".join(v.endorsements.get(p["id"], [])) or "nothing"
        lines.append(f"- {p['id']} {p['name']}: {p['one_liner']}. Backs {backs}.")
        tie = ledger.ties.get(p["id"])
        if tie and tie.ideas:
            lines.append(f"  Their ideas you know: {', '.join(tie.ideas[-4:])}")
        lines.append(f"  {ledger.summary(p['id'])}")
        if tie and tie.impression:
            lines.append(f'  Your impression of them: "{tie.impression}"')
        history = ledger.recent(p["id"], FULL_HISTORY if p["id"] in full else SHORT_HISTORY)
        if history:
            lines.append("  Between you, newest last:")
            lines += [f"    · {h}" for h in history]
    if not v.peers:
        lines.append("- nobody else")
    return Section("people", "\n".join(lines))


def pick_branches(v: View, limit: int = config.MAX_BRANCHES_IN_PROMPT) -> list[dict]:
    """The seed, branches in the inbox, branches with backers, then the newest."""
    wanted: list[str] = []

    def want(branch_id: str | None):
        if branch_id and branch_id in v.branches and branch_id not in wanted:
            wanted.append(branch_id)

    for b in v.branches.values():
        if b.get("op") == "seed":
            want(b["branch_id"])
    for env in v.inbox:
        body = env.get("body") or {}
        want((body.get("branch") or {}).get("branch_id"))
        for branch_id in body.get("branch_ids") or []:
            want(branch_id)
    for branch_id in v.my_endorsed:
        want(branch_id)
    for branch_id in sorted(v.branches, key=lambda b: -len(v.backers(b))):
        if v.backers(branch_id):
            want(branch_id)
    for branch_id in reversed(list(v.branches)):
        want(branch_id)
    return [v.branches[b] for b in wanted[:limit]]


def branches_section(v: View) -> Section:
    lines = ["Branches you know:"]
    for b in pick_branches(v):
        if b.get("op") == "seed":
            origin = "the seed"
        else:
            parents = " and ".join(b.get("parent_ids") or []) or "?"
            authors = ", ".join(v.name(a) for a in b.get("authors") or [])
            origin = f"{b['op']} of {parents} by {authors}"
            parent_authors = {
                a for pid in b.get("parent_ids") or [] for a in (v.branches.get(pid) or {}).get("authors") or []
            } - set(b.get("authors") or [])
            if parent_authors and config.on("credit"):
                origin += f", building on {', '.join(v.name(a) for a in sorted(parent_authors))}"
        backers = ", ".join(v.name(a) for a in v.backers(b["branch_id"])) or "nobody"
        lines.append(f'- {b["branch_id"]} ({origin}; backed by {backers}): "{clip(b["text"], config.BRANCH_TEXT_MAX)}"')
    return Section("branches", "\n".join(lines))


def memories_section(v: View) -> Section:
    lines = ["Your memories, most relevant first:"]
    lines += [f"[{m.id}] {m.text}" for m in v.memories] or ["(nothing yet)"]
    return Section("memories", "\n".join(lines))


def inbox_line(v: View, env: dict) -> str:
    body = env.get("body") or {}
    who = v.name(env.get("from"))
    msg = env.get("msg_id")
    if env["type"] == IDEA_SHARE:
        branch = body.get("branch") or {}
        return f'- msg {msg}: {who} shared {branch.get("branch_id")} with you. Note: "{clip(body.get("note"), 200)}"'
    if env["type"] == IDEA_PITCH:
        branch = body.get("branch") or {}
        return f'- msg {msg}: {who} pitches {branch.get("branch_id")} to you: "{clip(body.get("note"), 200)}"'
    if env["type"] == MERGE_PROPOSE:
        a, b = (body.get("branch_ids") or [None, None])[:2]
        return (
            f'- msg {msg}: {who} proposes merging {a} and {b} into: "{clip(body.get("text"), config.BRANCH_TEXT_MAX)}". '
            f'Note: "{clip(body.get("note"), 200)}"'
        )
    return f"- msg {msg}: {who} sent {env['type']}"


def inbox_section(v: View) -> Section:
    lines = ["Messages waiting for your reply:"]
    lines += [inbox_line(v, env) for env in v.inbox] or ["(none)"]
    return Section("inbox", "\n".join(lines))


def to_score_section(v: View) -> Section:
    lines = ["New since your last turn (rate each):" if config.on("rules") else "New observations to rate:"]
    lines += [f"[{o.id}] {o.text}" for o in v.to_score] or ["(none)"]
    return Section("to_score", "\n".join(lines))


def task_section(v: View) -> Section:
    if v.idle:
        text = (
            "Nothing new has reached you for a while. Take the initiative: twist a branch and share it, "
            "pitch the branch you champion, propose a merge, or back something."
        )
    else:
        text = "Reply to every waiting message, rate the new observations, then take your one action."
    return Section("task", text)


def decision_prompt(v: View) -> tuple[list[Section], list[Section]]:
    user = [
        world_section(v),
        people_section(v) if config.on("social") else town_section(v),
        branches_section(v),
        memories_section(v),
        inbox_section(v),
        to_score_section(v),
        task_section(v),
    ]
    return system_sections(v.me), user


def reflection_prompt(v: View, recent: list[Observation]) -> tuple[list[Section], list[Section]]:
    memories = "\n".join(f"[{m.id}] {m.text}" for m in recent)
    if config.on("reflect"):
        task = (
            "Step back and reflect on what has happened so far. Write two or three insights about the ideas and the "
            "other characters, each citing the ids of the memories it rests on. Then, for each character you've dealt "
            "with, one line on what you make of them now. Then your agenda: the idea you want to push next, and whose "
            "help or opposition matters for it."
        )
        return system_sections(v.me), [
            world_section(v),
            people_section(v) if config.on("social") else town_section(v),
            branches_section(v),
            Section("memories", f"Your most important recent memories:\n{memories}"),
            Section("task", task),
        ]
    task = (
        "Step back and reflect on what has happened so far. Write two or three insights about the ideas "
        "and the other characters, like who keeps proposing the same thing or whose taste clashes with yours. "
        "Then write your agenda: the branch you champion and who you want to win over."
    )
    return system_sections(v.me), [
        world_section(v),
        town_section(v),
        branches_section(v),
        Section("memories", f"Your most important recent memories:\n{memories}"),
        Section("task", task),
    ]


def last_words_prompt(v: View) -> tuple[list[Section], list[Section]]:
    task = (
        "Your life has run out. Write your last words: one final pitch for the idea you believe in, "
        "addressed to the whole town, in your own voice, under 280 characters."
    )
    return system_sections(v.me), [world_section(v), branches_section(v), memories_section(v), Section("task", task)]


# -- the mock brain -----------------------------------------------------------
# Plausible answers built from the persona, for runs with no LLM.


def mock_decision(v: View, rng: random.Random) -> Decision:
    me = v.me
    love, hate = rng.choice(me["loves"]), rng.choice(me["hates"])
    branches = list(v.branches.values())
    others = [b for b in branches if b.get("op") != "seed"]
    peers = [p["id"] for p in v.peers]
    action = rng.choices(["twist", "back", "pitch", "trash", "merge", "ignore"], [40, 25, 10, 8, 10, 7])[0]
    if not branches:
        action = "ignore"
    elif action in ("back", "pitch", "trash") and not others:
        action = "twist"
    elif action == "merge" and len(others) < 2:
        action = "twist"
    if action in ("pitch", "merge", "twist") and not peers:
        action = "back" if others else "ignore"

    decision = {
        "action": action, "target_branch": None, "other_branch": None, "text": None, "send_to": None,
        "note": None, "endorse": [], "unendorse": [], "flag_human": None,
    }
    if action == "twist":
        target = rng.choice(branches)
        decision.update(
            target_branch=target["branch_id"],
            text=clip(f"{clip(target['text'], 90)}, rebuilt around {love} and with no {hate}", 200),
            send_to=rng.choice(peers),
            note=f"This version has {love} in it. Tell me what you think.",
        )
    elif action == "merge":
        a, b = rng.sample(others, 2)
        decision.update(
            target_branch=a["branch_id"], other_branch=b["branch_id"],
            text=clip(f"{clip(a['text'], 90)}, combined with {clip(b['text'], 90)}", 200),
            send_to=next((x for x in b.get("authors", []) if x in peers), rng.choice(peers)),
            note="Our ideas are stronger together.",
        )
    elif action in ("back", "trash"):
        decision["target_branch"] = rng.choice(others)["branch_id"]
    elif action == "pitch":
        target = v.branches.get(v.my_endorsed[-1]) if v.my_endorsed else rng.choice(others)
        decision.update(target_branch=target["branch_id"], send_to=rng.choice(peers), note=f"Back this. It has {love}.")

    says = {
        "twist": f"What if it had more {love}?",
        "merge": "Two halves of a better idea.",
        "back": f"I'm backing this. It has {love}.",
        "trash": f"Too much {hate}. No.",
        "pitch": "Hear me out on this one.",
        "ignore": "Not yet. Let it steep.",
    }
    replies = []
    build = config.on("build")
    for env in v.inbox:
        body = env.get("body") or {}
        merge = env["type"] == MERGE_PROPOSE
        answer = rng.choice(["accept", "reject"] + (["counter"] if merge else []))
        base = body.get("text") or (body.get("branch") or {}).get("text") or ""
        sparked = answer == "counter" or (build and not merge and rng.random() < 0.3)
        idea = clip(f"{clip(base, 90)}, with {love}", 200) if sparked else None
        if build:
            replies.append(Reply(msg_id=env["msg_id"], answer=answer, text=f"Needs more {love}.", idea=idea))
        else:
            replies.append(ReplyV1(msg_id=env["msg_id"], answer=answer, text=idea or f"Needs more {love}."))
    return decision_model()(
        replies=replies,
        importance=[Score(obs_id=o.id, importance=rng.randint(2, 9)) for o in v.to_score],
        reason=f"{me['worldview']}",
        say=says[action],
        **decision,
    )


def mock_reflection(v: View, rng: random.Random, recent: list[Observation] | None = None):
    other = rng.choice(v.peers) if v.peers else None
    peer = other["name"] if other else "the town"
    champion = v.my_endorsed[-1] if v.my_endorsed else "the seed"
    insights = [f"{peer} keeps pulling the idea away from {rng.choice(v.me['loves'])}.", "The town agrees too easily."]
    agenda = f"Champion {champion} and win over {peer}."
    if not config.on("reflect"):
        return ReflectionV1(insights=insights, agenda=agenda)
    evidence = [m.id for m in (recent or [])[:2]]
    return Reflection(
        insights=[Insight(text=t, evidence=evidence) for t in insights],
        impressions=[Impression(peer_id=other["id"], impression=f"Cares about {rng.choice(other['loves'])} more than anything.")] if other else [],
        agenda=agenda,
    )


def mock_last_words(v: View, rng: random.Random) -> LastWords:
    return LastWords(text=f"{v.me['name']} signs off. Keep {rng.choice(v.me['loves'])} in it, whatever you build.")
