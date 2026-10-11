"""Step 7: tallying living support into finalists."""

from world.ledger import Ledger


def env(type, sender, **body):
    return {"type": type, "from": sender, "body": body}


def branch(branch_id, text="x"):
    return {"branch_id": branch_id, "text": text, "op": "twist", "author": "char_a"}


def test_top_three_by_living_support_with_reasons():
    ledger = Ledger()
    ledger.observe(env("endorse", "a", branch=branch("br_1", "oak chest"), reason="it lasts"))
    ledger.observe(env("endorse", "b", branch=branch("br_1", "rewritten"), reason="ritual"))
    ledger.observe(env("endorse", "c", branch_id="br_1"))
    ledger.observe(env("endorse", "d", branch_id="br_2"))
    ledger.observe(env("endorse", "e", branch_id="br_2"))
    ledger.observe(env("endorse", "f", branch_id="br_3"))
    ledger.observe(env("endorse", "a", branch_id="br_4"))
    result = ledger.finalists({"a", "b", "c", "d", "e", "f"})
    assert [f["branch_id"] for f in result["finalists"]] == ["br_1", "br_2", "br_3"]
    first = result["finalists"][0]
    assert first["support"] == 3
    assert first["branch"]["text"] == "oak chest"
    assert first["champions"][0] == {"agent_id": "a", "reason": "it lasts"}
    # br_3 and br_4 both have one backer, so the third slot is a tie
    assert result["ties"] == [["br_3", "br_4"]]


def test_only_living_backers_count():
    ledger = Ledger()
    ledger.observe(env("endorse", "a", branch_id="br_1"))
    ledger.observe(env("endorse", "b", branch_id="br_1"))
    ledger.observe(env("endorse", "c", branch_id="br_2"))
    ledger.observe(env("agent.last_words", "a", text="goodbye"))
    # b crashed: it is simply missing from the living set
    result = ledger.finalists({"c", "d"})
    assert [(f["branch_id"], f["support"]) for f in result["finalists"]] == [("br_2", 1)]


def test_two_endorsement_limit_and_unendorse():
    ledger = Ledger()
    for b in ("br_1", "br_2", "br_3"):
        ledger.observe(env("endorse", "a", branch_id=b))
    assert ledger.endorsements["a"] == ["br_2", "br_3"]
    ledger.observe(env("unendorse", "a", branch_id="br_2", reason="needs an app"))
    assert ledger.support({"a"}) == {"br_3": ["a"]}


def test_minority_report_comes_from_the_strongest_rival_faction():
    ledger = Ledger()
    ledger.observe(env("endorse", "a", branch_id="br_1"))
    ledger.observe(env("endorse", "b", branch_id="br_1"))
    ledger.observe(env("endorse", "c", branch_id="br_2"))
    ledger.observe(env("endorse", "d", branch_id="br_2"))
    ledger.observe(env("endorse", "e", branch_id="br_3"))
    ledger.observe(env("objection", "e", branch_id="br_1", reason="small faction gripe"))
    ledger.observe(env("objection", "c", branch_id="br_1", reason="it creates waste"))
    ledger.observe(env("objection", "a", branch_id="br_1", reason="a backer can't object"))
    result = ledger.finalists({"a", "b", "c", "d", "e"})
    br_1 = next(f for f in result["finalists"] if f["branch_id"] == "br_1")
    assert br_1["minority_report"] == {"agent_id": "c", "reason": "it creates waste"}
    assert next(f for f in result["finalists"] if f["branch_id"] == "br_2")["minority_report"] is None


def test_majority_rest_and_reset_when_support_moves():
    ledger = Ledger()
    living = {"a", "b", "c", "d"}
    ledger.observe(env("rest", "a"))
    ledger.observe(env("rest", "b"))
    assert not ledger.majority_resting(living)  # 2 of 4 is not a majority
    ledger.observe(env("rest", "c"))
    assert ledger.majority_resting(living)
    ledger.observe(env("endorse", "d", branch_id="br_1"))
    assert not ledger.majority_resting(living)
    # dead agents don't count toward the living total
    for a in ("a", "b"):
        ledger.observe(env("rest", a))
    ledger.observe(env("agent.last_words", "c"))
    assert ledger.majority_resting(living - {"c"})


def test_ignores_garbage():
    ledger = Ledger()
    ledger.observe({"type": "endorse"})
    ledger.observe(env("endorse", "a"))
    ledger.observe(env("whatever", "a", branch_id="br_1"))
    assert ledger.finalists({"a"}) == {"finalists": [], "ties": []}
