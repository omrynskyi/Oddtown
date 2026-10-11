"""Proposals, composting and the branch budget: what pushes the town to converge instead of branching forever.
All three are worked out from public state (backing and branch.withdraw broadcasts), so each test looks at one character."""

import asyncio

from agents import brain
from agents.messages import ENDORSE, IDEA_REACTION, IDEA_SHARE, WITHDRAW
from agents.tests.test_social import character
from agents.tests.test_units import CAST, make_view

ME, DEZ, ANSELM, PRIYA = (p["id"] for p in CAST[:4])
SEED = {"branch_id": "br_seed", "parent_ids": [], "op": "seed", "authors": ["world"], "text": "a coffee subscription"}


def twist(branch_id: str, *authors: str) -> dict:
    return {"branch_id": branch_id, "parent_ids": ["br_seed"], "op": "twist", "authors": list(authors), "text": f"idea {branch_id}"}


def ids(view) -> list[str]:
    return [b["branch_id"] for b in brain.pick_branches(view)]


def prompt(view) -> str:
    system, user = brain.decision_prompt(view)
    return "\n".join(s.text for s in system + user)


def withdrawn(town) -> list[tuple[str, str]]:
    return [(env["body"]["branch_id"], env["body"]["why"]) for _, env in town.sent if env["type"] == WITHDRAW]


def answer(c, peer: str, branch_id: str, answer: str) -> None:
    asyncio.run(c.on_envelope({"type": IDEA_REACTION, "from": peer, "body": {"branch_id": branch_id, "answer": answer, "text": "hm"}}))


def test_proposals_count_living_backers_besides_the_authors_and_come_first():
    merge = {**twist("br_m", DEZ, ANSELM), "op": "merge"}
    branches = {"br_seed": SEED, "br_m": merge, "br_x": twist("br_x", PRIYA)}
    view = make_view(branches=branches, endorsements={DEZ: ["br_m"], ANSELM: ["br_m"], PRIYA: ["br_m", "br_x"]}, inbox=[])
    assert not view.proposal("br_m")  # its own two authors don't count

    view.endorsements[ME] = ["br_m"]
    assert view.proposal("br_m")
    assert ids(view)[:2] == ["br_seed", "br_m"]
    assert "- br_m [proposal] (" in prompt(view) and "br_m (4 backers, a proposal)" in prompt(view)

    view.peers = [p for p in view.peers if p["id"] != PRIYA]  # Priya died, so her backing no longer counts
    assert not view.proposal("br_m")


def test_a_branch_everyone_turned_down_fades_and_backing_brings_it_back(monkeypatch):
    monkeypatch.setenv("ODDTOWN_MODEL", "mock")
    c, town = character()
    unliked = c.new_branch("twist", ["br_seed"], [c.id], "Coffee by canal boat")["branch_id"]
    liked = c.new_branch("twist", ["br_seed"], [c.id], "Coffee by night train")["branch_id"]
    for branch_id in (unliked, liked):
        c.showed(branch_id, DEZ)
        c.showed(branch_id, PRIYA)
        answer(c, DEZ, branch_id, "reject")
    asyncio.run(c.compost_unliked())
    assert withdrawn(town) == []  # Priya hasn't answered yet

    answer(c, PRIYA, unliked, "reject")
    answer(c, PRIYA, liked, "accept")
    asyncio.run(c.take_turn(idle=True))  # a turn starts by withdrawing what nobody liked
    assert withdrawn(town) == [(unliked, "unliked")]
    assert c.composted(unliked) and not c.composted(liked)
    assert unliked not in ids(c.view(CAST[1:], [], False))
    assert unliked not in [b["branch_id"] for b in c.briefing()["branches"]]

    dez, _ = character(1)
    dez.learn(c.branches[unliked])
    asyncio.run(dez.on_envelope({"type": WITHDRAW, "from": c.id, "body": {"branch_id": unliked, "why": "unliked"}}))
    assert dez.composted(unliked)
    asyncio.run(dez.on_envelope({"type": ENDORSE, "from": ANSELM, "body": {"branch": c.branches[unliked]}}))
    assert not dez.composted(unliked)  # backing it brings it back


def test_a_fourth_unbacked_branch_retires_the_oldest():
    c, town = character()
    made = [c.new_branch("twist", ["br_seed"], [c.id], f"idea {i}")["branch_id"] for i in range(3)]
    world = brain.world_section(c.view(CAST[1:], [], False)).text
    assert f"Your unbacked branches: {', '.join(made)} (3 of 3; a new one retires {made[0]})" in world

    d = brain.Decision(
        reason="r", replies=[], importance=[], action="twist", target_branch="br_seed", other_branch=None,
        text="a fourth idea", send_to=DEZ, note="look", endorse=[], unendorse=[], flag_human=None, say="hi",
    )
    asyncio.run(c.apply(d, c.view(CAST[1:], [], False), []))
    assert withdrawn(town) == [(made[0], "budget")] and town.sent[-1][1]["type"] == IDEA_SHARE
    assert c.composted(made[0]) and c.my_unbacked()[:2] == made[1:]


def test_a_dying_character_withdraws_its_unbacked_branches_unless_a_co_author_lives(monkeypatch):
    monkeypatch.setenv("ODDTOWN_MODEL", "mock")
    c, town = character()
    solo = c.new_branch("twist", ["br_seed"], [c.id], "solo")["branch_id"]
    c.new_branch("merge", ["br_seed"], [DEZ, c.id], "shared with Dez")
    backed = c.new_branch("twist", ["br_seed"], [c.id], "backed")["branch_id"]
    c.public_endorse(ANSELM, backed)
    asyncio.run(c.die())
    assert withdrawn(town) == [(solo, "died")]


def test_a_composted_branch_still_shows_when_it_is_in_the_inbox():
    view = make_view(endorsements={}, retired={"br_aa"})
    assert view.composted("br_aa") and "br_aa" in ids(view)
    view.inbox = []
    assert "br_aa" not in ids(view)


def test_switching_all_three_off_gives_back_todays_prompt(monkeypatch):
    monkeypatch.setenv("ODDTOWN_ABLATE", "quorum,compost,budget")
    branches = {"br_seed": SEED, "br_p": twist("br_p", DEZ), **{f"br_m{i}": twist(f"br_m{i}", ME) for i in range(4)}}
    text = prompt(make_view(branches=branches, endorsements={ANSELM: ["br_p"], PRIYA: ["br_p"]}))
    assert brain.system_sections(CAST[0])[0].text == brain.RULES
    assert "[proposal]" not in text and ", a proposal" not in text and "Your unbacked" not in text
