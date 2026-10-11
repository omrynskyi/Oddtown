"""Picking the top N ideas in the ideation voting loop."""

import random

import pytest
from pydantic import ValidationError

from world.ideation import LoopSettings, eligible, select_top


def pool_with(votes_list):
    """Ideas i00, i01, ... with the given vote counts."""
    pool = [f"i{n:02d}" for n in range(len(votes_list))]
    return pool, dict(zip(pool, votes_list))


def test_the_example_top_five_plus_one_random_from_the_ones():
    pool, votes = pool_with([6, 6, 5, 3, 3] + [1] * 6 + [0] * 25)  # 36 ideas
    selected, random_fill = select_top(pool, votes, 6, random.Random(1))
    assert selected[:5] == ["i00", "i01", "i02", "i03", "i04"]
    assert len(selected) == 6 and len(random_fill) == 1
    assert random_fill[0] in [f"i{n:02d}" for n in range(5, 11)]  # one of the 1s
    assert selected[5] == random_fill[0]


def test_exact_fit_draws_nothing_at_random():
    pool, votes = pool_with([3, 2, 1, 0, 0])
    assert select_top(pool, votes, 3, random.Random(1)) == (["i00", "i01", "i02"], [])


def test_tie_at_the_top_bigger_than_n_is_all_random():
    pool, votes = pool_with([2, 2, 2, 2, 0])
    selected, random_fill = select_top(pool, votes, 3, random.Random(1))
    assert len(selected) == 3 and set(selected) <= {"i00", "i01", "i02", "i03"}
    assert sorted(random_fill) == sorted(selected)


def test_fewer_voted_ideas_than_n_fill_from_zero_votes():
    # 3 agents cast only 3 votes, N=6
    pool, votes = pool_with([2, 1] + [0] * 7)
    selected, random_fill = select_top(pool, votes, 6, random.Random(1))
    assert selected[:2] == ["i00", "i01"]
    assert len(selected) == 6 and len(random_fill) == 4
    assert all(votes[i] == 0 for i in random_fill)


def test_pool_smaller_than_n_returns_everything():
    pool, votes = pool_with([1, 0])
    assert sorted(select_top(pool, votes, 3, random.Random(1))[0]) == ["i00", "i01"]


def test_same_seed_same_picks_any_pool_order():
    pool, votes = pool_with([1] * 20)
    a = select_top(pool, votes, 4, random.Random(42))
    b = select_top(list(reversed(pool)), votes, 4, random.Random(42))
    assert a == b


def test_eligible_drops_everything_the_voter_last_edited():
    pool = [{"idea_id": "a", "author": "x"}, {"idea_id": "b", "author": "y"}, {"idea_id": "c", "author": "x"}]
    assert [i["idea_id"] for i in eligible(pool, "x")] == ["b"]


@pytest.mark.parametrize("field,bad", [("agents", 2), ("agents", 13), ("rounds", 2), ("rounds", 11),
                                       ("top_n", 2), ("top_n", 7)])
def test_settings_bounds(field, bad):
    with pytest.raises(ValidationError):
        LoopSettings(**{field: bad})


@pytest.mark.parametrize("field,ok", [("agents", 3), ("agents", 12), ("rounds", 3), ("rounds", 10),
                                      ("top_n", 3), ("top_n", 6)])
def test_settings_bounds_inclusive(field, ok):
    assert getattr(LoopSettings(**{field: ok}), field) == ok
