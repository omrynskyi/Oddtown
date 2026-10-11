"""Designing a replacement for an agent that died: pick, propose, refine, refine, birth.

Shared by the town's Character and the ideation loop's IdeationAgent, so the
two modes replace agents with the same mechanics and the same prompts.

1. Pick. Three random survivors. The first proposes, the next two refine.
2. Propose. A full persona for someone as unlike the living group as possible.
3. Refine, twice. Each refiner changes it once through its own worldview.
4. Birth. The last designer runs the schema and content checks and asks the
   world service to start the newcomer, with a briefing on what it missed.

If a designer dies before the draft reaches it, the next one in line skips that
step, and a watchdog lets a later designer take over the proposal.

The chain lives on a host agent and only uses this much of it:

    id, name, persona, rng, alive, known (id -> persona), dead (set of ids)
    await living_peers(), await world_id(), briefing()
    await think(kind, (system, user), out, mock)  -> llm.Result
    await send(to, type, body, observe=True)      -> bool, False if the peer was dead
    emit(type, payload), spawn(coro)
    remember(text, kind)          a memory, or nothing
    spend_design_step()           a design step costs the host a turn of life, or nothing
    enqueue_design(env)           design work the host assigned itself
    await on_design_failed(persona, body, check, reason)   check is "schema" or "content"
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import deque

from pydantic import ValidationError

from agents import config, replace
from agents.brain import clip
from agents.llm import LLMError
from agents.messages import PERSONA_DRAFT, REPLACE_REQUEST, SPAWN_REQUEST
from agents.schema import validate_persona

log = logging.getLogger(__name__)


class DesignChain:
    def __init__(self, host):
        self.host = host
        self.designs: deque[dict] = deque()  # replacement work, done before the next decision
        self.drafts_seen: set[str] = set()  # replacements whose draft reached us
        self.pending_reviews: list[dict] = []  # newcomers waiting on a human after a failed content check

    async def pick_designers(self, exclude: set[str], include_me: bool, peers: list[dict] | None = None) -> list[str]:
        """Three random survivors. The first proposes, the next two refine, in that order."""
        h = self.host
        peers = peers if peers is not None else await h.living_peers()
        candidates = [p["id"] for p in peers if p["id"] not in exclude and p["id"] not in h.dead]
        if include_me and h.alive:
            candidates.append(h.id)
        return h.rng.sample(candidates, min(3, len(candidates)))

    async def request_replacement(self, replaces: str, replaces_name: str, order: list[str], generation: int) -> None:
        h = self.host
        briefing = h.briefing()
        for i, designer in enumerate(order):
            body = {
                "replaces": replaces, "replaces_name": replaces_name, "role": "propose" if i == 0 else "refine",
                "order": order, "generation": generation, "briefing": briefing,
            }
            if designer == h.id:
                h.enqueue_design({"type": REPLACE_REQUEST, "from": h.id, "body": body})
            else:
                await h.send(designer, REPLACE_REQUEST, body)

    async def design(self, env: dict) -> None:
        body = env.get("body") or {}
        kind = env.get("type")
        try:
            if kind == REPLACE_REQUEST and body.get("role") == "propose":
                await self.propose(body, next_step=1)
            elif kind == REPLACE_REQUEST:
                self.host.remember(f"You were picked to help design {body.get('replaces_name')}'s replacement", "replacement")
                self.host.spawn(self.design_watchdog(body))
            elif kind == PERSONA_DRAFT:
                await self.refine(body)
            elif kind == "takeover":
                await self.propose(body, next_step=body["order"].index(self.host.id) + 1)
        except LLMError:
            pass  # the next designer in line, or the watchdog, carries on

    async def design_town(self, exclude: str | None = None) -> list[dict]:
        peers = await self.host.living_peers()
        return [p for p in peers if p["id"] != exclude] + [self.host.persona]

    def newcomer_model(self) -> str:
        return config.model_override() or self.host.rng.choice(config.allowed_models())

    async def propose(self, body: dict, next_step: int) -> None:
        h = self.host
        town = await self.design_town()
        taken = target = None
        if config.on("design"):
            taken = sorted({p.get("name") for p in [*h.known.values(), h.persona] if p.get("name")})
            target = replace.empty_corner(town, h.rng)
        if config.on("design"):
            result = await h.think(
                "propose",
                replace.propose_prompt(h.persona, town, body["replaces_name"], body.get("briefing") or {}, taken, target),
                replace.Proposal,
                lambda: replace.Proposal(concepts=["a", "b", "c"], names=[], draft=replace.mock_propose(town, h.rng)),
            )
            draft = result.value.draft
            body = {**body, "names": [n for n in result.value.names if n][:5]}
            draft.name = replace.free_name(draft.name, body["names"], taken)
        else:
            result = await h.think(
                "propose",
                replace.propose_prompt(h.persona, town, body["replaces_name"], body.get("briefing") or {}),
                replace.PersonaDraft,
                lambda: replace.mock_propose(town, h.rng),
            )
            draft = result.value
        persona = replace.to_persona(
            draft, model=self.newcomer_model(), replaces=body["replaces"], designers=body["order"], generation=body["generation"],
        )
        h.spend_design_step()
        self.drafts_seen.add(body["replaces"])
        h.remember(f"You proposed {persona['name']} ({persona['one_liner']}) to replace {body['replaces_name']}", "replacement")
        h.emit("replace.step", {"step": "propose", "replaces": body["replaces"], "persona": persona})
        await self.forward_draft(persona, body, next_step, [f"{h.name} proposed {persona['name']}"])

    async def refine(self, body: dict, feedback: str | None = None) -> None:
        h = self.host
        draft = body["draft"]
        town = await self.design_town(exclude=draft.get("id"))
        if feedback is None and config.on("design"):
            people = [p for p in [*h.known.values(), h.persona] if p.get("id") != draft.get("id")]
            feedback = replace.gaps_feedback(draft, people, sorted({p.get("name") for p in people if p.get("name")}))
        result = await h.think(
            "refine",
            replace.refine_prompt(h.persona, draft, town, body["replaces_name"], body.get("changes") or [], feedback),
            replace.Refined,
            lambda: replace.mock_refine(draft, town, h.rng),
        )
        persona = replace.to_persona(
            result.value.draft, model=draft.get("model") or self.newcomer_model(), replaces=body["replaces"],
            designers=body["order"], generation=body["generation"], persona_id=draft.get("id"),
        )
        h.spend_design_step()
        change = f"{h.name}: {clip(result.value.change, 150)}"
        h.remember(f"You refined {persona['name']}, the replacement for {body['replaces_name']}: {clip(result.value.change, 100)}", "replacement")
        h.emit("replace.step", {"step": "refine", "replaces": body["replaces"], "persona": persona, "change": change})
        await self.forward_draft(persona, body, body.get("step", 0) + 1, (body.get("changes") or []) + [change])

    async def forward_draft(self, persona: dict, body: dict, step: int, changes: list[str]) -> None:
        """Hand the draft to the next living designer, or birth it if nobody is left."""
        h = self.host
        order = body["order"]
        for i in range(step, len(order)):
            if order[i] == h.id:
                continue
            payload = {
                "replaces": body["replaces"], "replaces_name": body["replaces_name"], "order": order, "step": i,
                "generation": body["generation"], "draft": persona, "changes": changes, "briefing": body.get("briefing") or {},
                "names": body.get("names") or [],
            }
            if await h.send(order[i], PERSONA_DRAFT, payload):
                return
        await self.birth(persona, {**body, "changes": changes})

    async def design_watchdog(self, body: dict) -> None:
        """If every designer before us dies before a draft arrives, we take over the proposal."""
        h = self.host
        await asyncio.sleep(config.scaled(config.DESIGN_TIMEOUT_S))
        if body["replaces"] in self.drafts_seen or not h.alive:
            return
        order = body["order"]
        mine = order.index(h.id) if h.id in order else -1
        living = {p["id"] for p in await h.living_peers()}
        if mine <= 0 or any(d in living for d in order[:mine]):
            return
        h.enqueue_design({"type": "takeover", "from": h.id, "body": body})

    async def birth(self, persona: dict, body: dict) -> None:
        h = self.host
        if config.on("design"):
            # Refiners may have renamed it into a clash. The proposer's other names are the way out.
            taken = sorted({p.get("name") for p in h.known.values() if p.get("name") and p.get("id") != persona.get("id")} | {h.name})
            persona = {**persona, "name": replace.free_name(persona["name"], body.get("names") or [], taken)}
        try:
            validate_persona(persona)
        except ValidationError as exc:
            log.warning("replacement for %s failed the schema check: %s", body["replaces"], exc)
            h.emit("replace.failed", {"replaces": body["replaces"], "error": str(exc)[:500]})
            await h.on_design_failed(persona, body, "schema", str(exc)[:300])
            return
        try:
            check = (await h.think(
                "content_check", replace.content_check_prompt(persona), replace.ContentCheck,
                # The mock passes everyone, unless a test wants to see a rejection handled.
                lambda: replace.ContentCheck(ok=not os.environ.get("ODDTOWN_MOCK_REJECT_PERSONAS"), reason="mock check"),
            )).value
            ok, reason = check.ok, check.reason
        except LLMError:
            ok, reason = False, "the content check failed to run"
        if not ok:
            await h.on_design_failed(persona, body, "content", reason)
            return
        await self.spawn_newcomer(persona)

    async def spawn_newcomer(self, persona: dict) -> bool:
        h = self.host
        world = await h.world_id()
        if world and await h.send(world, SPAWN_REQUEST, {"persona": {**persona, "briefing": h.briefing()}}, observe=False):
            h.remember(f"You brought {persona['name']} to life", "replacement")
            h.emit("replace.step", {"step": "birth", "replaces": persona["lineage"]["replaces"], "persona": persona})
            return True
        return False

    def on_human_answer(self, text: str) -> None:
        """We never learn the request_id the world assigned to our request, so the oldest review takes the answer."""
        if not self.pending_reviews:
            return
        review = self.pending_reviews.pop(0)
        if text.strip().lower().startswith(("approve", "yes", "ok", "allow")):
            self.host.spawn(self.spawn_newcomer(review["persona"]))
