"""Tests for the mechanisms that shape how characters deal with each other:
relevance retrieval, the relationship ledger, build replies, credit, grounded
reflection, the replacement design gaps and the ablation switches."""

import asyncio
import json
import random
from pathlib import Path

import pytest

from agents import brain, replace
from agents.brain import Reflection, View
from agents.character import Character
from agents.memory import MemoryStream
from agents.messages import IDEA_BUILT, IDEA_REACTION, IDEA_SHARE, REPLACE_REQUEST
from agents.schema import nearest
from agents.social import Ledger

ROOT = Path(__file__).resolve().parents[2]
CAST = json.loads((ROOT / "personas" / "starting_cast.json").read_text())


class StubTown:
    """Records what a character sends, and lists the cast as living."""

    def __init__(self, living):
        self.sent = []
        self.living = living
        self.registry = self

    async def send(self, to, env):
        self.sent.append((to, env))

    async def broadcast(self, env):
        self.sent.append(("*", env))
        return []

    async def characters(self):
        return [{"id": p["id"], "persona": p} for p in self.living]

    async def search(self, kind):
        return [{"id": "world"}]


def character(me=0) -> tuple[Character, StubTown]:
    town = StubTown(CAST)
    c = Character(CAST[me], "run_test", town)
    c.known = {p["id"]: p for p in CAST}
    c.seed = "a coffee subscription"
    c.branches["br_seed"] = {"branch_id": "br_seed", "parent_ids": [], "op": "seed", "authors": ["world"], "text": "a coffee subscription"}
    return c, town


# -- retrieval ----------------------------------------------------------------


def test_relevance_brings_back_an_old_memory_about_who_just_wrote():
    m = MemoryStream()
    old = m.add("Dez rejected your br_aa: pigeons only", "reaction", importance=3, about=("char_dez", "br_aa"))
    for i in range(30):
        m.step += 1
        m.add(f"something else happened {i}", "endorsement", about=("char_rook",))
    assert old not in m.retrieve(k=8)
    assert old in m.retrieve(k=8, focus={"char_dez"})


# -- the ledger and the people section ----------------------------------------


def test_people_section_shows_the_record_between_you():
    ledger = Ledger()
    dez = CAST[1]["id"]
    ledger.note(dez, 0, 'you showed them br_aa: "try it"')
    ledger.note(dez, 1, 'they rejected your br_aa: "no route"')
    ledger.answer(dez, mine=True, yes=False)
    ledger.tie(dez).impression = "Only cares about routes"
    view = View(
        me=CAST[0], seed="s", constraints=[], life_left=3, life_max=5, seconds_left=60, agenda="", my_endorsed=[],
        peers=CAST[1:], endorsements={}, branches={}, ledger=ledger, focus={dez},
    )
    text = brain.people_section(view).text
    assert "they rejected your br_aa" in text and "they took up 0 of your ideas and turned down 1" in text
    assert "Only cares about routes" in text
    assert text.count("You haven't dealt with each other yet.") == len(CAST) - 2


def test_incoming_messages_land_in_the_ledger_and_answers_wake_the_sender():
    c, _ = character()
    dez = CAST[1]["id"]
    asyncio.run(c.on_envelope({"type": IDEA_REACTION, "from": dez, "body": {"branch_id": "br_x", "answer": "reject", "text": "no route"}}))
    tie = c.social.ties[dez]
    assert tie.they_no == 1 and "rejected your br_x" in tie.lines[-1][1]
    assert c.news and c.wake.is_set() and dez in c.heard_from


# -- build replies and credit -------------------------------------------------


def test_a_build_reply_makes_a_credited_branch_and_sends_it_back():
    c, town = character()
    dez = CAST[1]["id"]
    theirs = {"branch_id": "br_dez1", "parent_ids": ["br_seed"], "op": "twist", "authors": [dez], "text": "Pigeon coffee"}
    env = {"type": IDEA_SHARE, "msg_id": "m1", "from": dez, "body": {"branch": theirs, "note": "fast"}}
    asyncio.run(c.on_envelope(env))
    reply = brain.Reply(msg_id="m1", answer="reject", text="Too fast, but", idea="Pigeons fly coffee to lighthouses")
    asyncio.run(c.reply(env, reply))
    to, sent = town.sent[-1]
    built = sent["body"]["branch"]
    assert to == dez and sent["type"] == IDEA_REACTION and sent["body"]["answer"] == "reject"
    assert built["parent_ids"] == ["br_dez1"] and built["authors"] == [c.id] and built["branch_id"] in c.branches
    assert c.social.ties[dez].you_built == 1


def test_twisting_someone_elses_branch_tells_its_author():
    c, town = character()
    rook = CAST[5]["id"]
    c.branches["br_rook"] = {"branch_id": "br_rook", "parent_ids": ["br_seed"], "op": "twist", "authors": [rook], "text": "Vent coffee"}
    branch = c.new_branch("twist", ["br_rook"], [c.id], "Vent coffee in stone jars")
    asyncio.run(c.credit("br_rook", branch, skip=CAST[1]["id"]))
    assert (rook, IDEA_BUILT) in [(to, env["type"]) for to, env in town.sent]


# -- reflection ---------------------------------------------------------------


def test_reflection_drops_insights_with_no_evidence_and_keeps_impressions():
    c, _ = character()
    real = c.memory.add("Dez rejected your br_x", "reaction", importance=7, about=(CAST[1]["id"],))
    made_up = Reflection(
        insights=[
            brain.Insight(text="Dez never answers anyone", evidence=["obs_999"]),
            brain.Insight(text="Dez wants routes, not ritual", evidence=[real.id]),
        ],
        impressions=[brain.Impression(peer_id=CAST[1]["id"], impression="Stubborn about routes")],
        agenda="Win Dez over",
    )

    async def fake_think(*args, **kwargs):
        return type("R", (), {"value": made_up})()

    c.think = fake_think
    c.memory._importance_since_reflection = 100
    asyncio.run(c.reflect())
    texts = [o.text for o in c.memory.items if o.kind == "reflection"]
    assert "Dez wants routes, not ritual" in texts and "Dez never answers anyone" not in texts
    assert c.social.ties[CAST[1]["id"]].impression == "Stubborn about routes"


# -- replacement design --------------------------------------------------------


def test_the_empty_corner_is_far_from_everyone():
    corner = replace.empty_corner(CAST, random.Random(0))
    _, d = nearest(corner, CAST)
    assert d > 1.0


def test_a_dying_character_finishes_design_work_and_the_last_one_designs_its_own_successor(monkeypatch):
    monkeypatch.setenv("ODDTOWN_MODEL", "mock")
    c, town = character()
    town.living = [c.persona]  # everyone else is gone
    c.alive = True
    asyncio.run(c.die())
    kinds = [env["type"] for _, env in town.sent]
    assert "spawn.request" in kinds  # it proposed, checked and birthed a newcomer before leaving


# -- ablation ------------------------------------------------------------------


def test_ablating_everything_gives_back_the_original_prompt(monkeypatch):
    monkeypatch.setenv("ODDTOWN_ABLATE", "all")
    fields = list(brain.decision_model().model_json_schema()["properties"])
    assert fields[0] == "replies" and fields[-2:] == ["reason", "say"]
    assert '"idea"' not in json.dumps(brain.decision_model().model_json_schema())
    assert brain.system_sections(CAST[0])[0].text == brain.RULES_V1
    assert brain.reflection_model() is brain.ReflectionV1


def test_a_twist_that_repeats_a_replys_idea_reuses_its_branch(monkeypatch):
    monkeypatch.setenv("ODDTOWN_MODEL", "mock")
    c, town = character()
    dez, rook = CAST[1]["id"], CAST[5]["id"]
    theirs = {"branch_id": "br_dez1", "parent_ids": ["br_seed"], "op": "twist", "authors": [dez], "text": "Pigeon coffee"}
    env = {"type": IDEA_SHARE, "msg_id": "m1", "from": dez, "body": {"branch": theirs, "note": "fast"}}
    asyncio.run(c.on_envelope(env))
    idea = "Pigeons fly coffee to lighthouses on the coast"
    d = brain.Decision(
        reason="r", replies=[brain.Reply(msg_id="m1", answer="accept", text="yes", idea=idea)], importance=[],
        action="twist", target_branch="br_dez1", other_branch=None, text=idea + "!", send_to=rook, note="look",
        endorse=[], unendorse=[], flag_human=None, say="hi",
    )
    view = c.view(CAST[1:], [env], False)
    asyncio.run(c.apply(d, view, [env]))
    made = [b for b in c.branches.values() if b.get("parent_ids") == ["br_dez1"]]
    assert len(made) == 1
    assert (rook, IDEA_SHARE) in [(to, e["type"]) for to, e in town.sent]


def test_a_clashing_name_gives_way_to_the_first_free_option():
    taken = ["Elara Voss", "Silas Vane"]
    assert replace.free_name("Elara Quist", ["Silas Moor", "Tomasz Okafor"], taken) == "Tomasz Okafor"
    assert replace.free_name("Ines Brandt", ["x"], taken) == "Ines Brandt"
