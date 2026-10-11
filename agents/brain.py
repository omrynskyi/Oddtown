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

from pydantic import BaseModel, Field

from agents import config
from agents.llm import Section
from agents.memory import Observation
from agents.messages import IDEA_PITCH, IDEA_SHARE, MERGE_PROPOSE

RULES = """You are one character in Oddtown, a town of AI characters with clashing worldviews who argue over how to twist one seed idea into something more creative. Ideas spread only by gossip between characters. Nobody runs the town and nobody owns the idea. When a character dies, three survivors design its replacement, so nobody holds the idea forever.

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


# -- output schemas -----------------------------------------------------------


class Reply(BaseModel):
    msg_id: str
    answer: Literal["accept", "reject", "counter"]
    text: str = Field(description="Your reason, or your merged wording when you counter")


class Score(BaseModel):
    obs_id: str
    importance: int = Field(description="1 to 10")


class HumanFlag(BaseModel):
    kind: Literal["rule_break", "feasibility"]
    branch_id: str
    claim: str


class Decision(BaseModel):
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
    reason: str
    say: str = Field(description="Speech bubble, under 100 characters")


class Reflection(BaseModel):
    insights: list[str] = Field(description="Two or three insights about the ideas and the other characters")
    agenda: str = Field(description="The branch you champion and who you want to win over, one or two sentences")


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
    return [Section("rules", RULES), Section("persona", persona_text(me))]


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
    return Section("world", "\n".join(lines))


def town_section(v: View) -> Section:
    lines = ["Living characters:"]
    for p in v.peers:
        backs = ", ".join(v.endorsements.get(p["id"], [])) or "nothing"
        lines.append(f"- {p['id']} {p['name']}: {p['one_liner']}. Backs {backs}.")
    if not v.peers:
        lines.append("- nobody else")
    return Section("town", "\n".join(lines))


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
    lines = ["New observations to rate:"]
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
        town_section(v),
        branches_section(v),
        memories_section(v),
        inbox_section(v),
        to_score_section(v),
        task_section(v),
    ]
    return system_sections(v.me), user


def reflection_prompt(v: View, recent: list[Observation]) -> tuple[list[Section], list[Section]]:
    memories = "\n".join(f"[{m.id}] {m.text}" for m in recent)
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
    for env in v.inbox:
        answer = rng.choice(["accept", "reject"] + (["counter"] if env["type"] == MERGE_PROPOSE else []))
        text = f"Needs more {love}." if answer != "counter" else clip(f"{(env.get('body') or {}).get('text', '')}, with {love}", 200)
        replies.append(Reply(msg_id=env["msg_id"], answer=answer, text=text))
    return Decision(
        replies=replies,
        importance=[Score(obs_id=o.id, importance=rng.randint(2, 9)) for o in v.to_score],
        reason=f"{me['worldview']}",
        say=says[action],
        **decision,
    )


def mock_reflection(v: View, rng: random.Random) -> Reflection:
    peer = rng.choice(v.peers)["name"] if v.peers else "the town"
    champion = v.my_endorsed[-1] if v.my_endorsed else "the seed"
    return Reflection(
        insights=[f"{peer} keeps pulling the idea away from {rng.choice(v.me['loves'])}.", "The town agrees too easily."],
        agenda=f"Champion {champion} and win over {peer}.",
    )


def mock_last_words(v: View, rng: random.Random) -> LastWords:
    return LastWords(text=f"{v.me['name']} signs off. Keep {rng.choice(v.me['loves'])} in it, whatever you build.")
