"""The single-LLM baseline: one call, same seed and constraints, three ideas.

It uses one of the characters' models, so the comparison is about the town,
not the model.

    from agents.baseline import run_baseline
    ideas = await run_baseline("a coffee subscription", ["it has to work with no electricity"])

    python -m agents.baseline "a coffee subscription" --constraint "no electricity"
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from pydantic import BaseModel, Field

from agents import config
from agents.llm import Section, call_json

CAST = Path(__file__).resolve().parent.parent / "personas" / "starting_cast.json"


class Ideas(BaseModel):
    ideas: list[str] = Field(description="Exactly three ideas, each one or two sentences")


def cast_model() -> str:
    try:
        return json.loads(CAST.read_text())[0]["model"]
    except (OSError, ValueError, LookupError, TypeError):
        return config.DEFAULT_MODEL


def mock_ideas(seed: str) -> Ideas:
    return Ideas(ideas=[
        f"{seed} with personalized picks chosen by AI",
        f"{seed} with a loyalty program and member rewards",
        f"{seed} for offices, billed per team",
    ])


async def run_baseline(seed: str, constraints: list[str] | tuple[str, ...] = (), model: str | None = None) -> list[str]:
    """Three ideas from one call. Raises agents.llm.LLMError if the call fails."""
    rules = "You are a creative product thinker."
    lines = [f"Seed idea: {seed}"]
    if constraints:
        lines.append("Constraints: " + " | ".join(constraints))
    lines.append("Give your three most creative takes on this idea, each one or two sentences.")
    result = await call_json(
        kind="baseline",
        model=model or cast_model(),
        system=[Section("rules", rules)],
        user=[Section("task", "\n".join(lines))],
        out=Ideas,
        mock=lambda: mock_ideas(seed),
    )
    return [" ".join(i.split()) for i in result.value.ideas if i.strip()][:3]


def main() -> None:
    parser = argparse.ArgumentParser(description="One LLM's three takes on a seed idea")
    parser.add_argument("seed")
    parser.add_argument("--constraint", action="append", default=[])
    parser.add_argument("--model")
    args = parser.parse_args()
    for idea in asyncio.run(run_baseline(args.seed, args.constraint, args.model)):
        print(f"- {idea}")


if __name__ == "__main__":
    main()
