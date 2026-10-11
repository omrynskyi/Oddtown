"""Starts one process per character and notices when one exits.

A character process is `python -m $AGENT_MODULE` (agents.character by default)
run from the repo root, with these in its environment on top of the world's:

    AGENT_ID       the persona's id
    PERSONA_FILE   path to the persona JSON
    RUN_ID         the run it belongs to
    ODDTOWN_TOWN   the town namespace for SLIM names and Directory records
    TRANSPORT, REGISTRY and the rest of .env, passed through

It joins the town itself with transport.town.Town.from_env(AGENT_ID).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import sys
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

ExitCallback = Callable[[str, int], Awaitable[None]]

# Spawned personas are LLM-written and the id becomes a file name and a SLIM name.
AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class Launcher:
    def __init__(self, run_dir: Path, env: dict[str, str], on_exit: ExitCallback, module: str | None = None):
        self.run_dir = Path(run_dir)
        self.env = env
        self.on_exit = on_exit
        self.module = module or os.environ.get("AGENT_MODULE", "agents.character")
        self.procs: dict[str, asyncio.subprocess.Process] = {}
        self._watchers: set[asyncio.Task] = set()
        (self.run_dir / "personas").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "logs").mkdir(parents=True, exist_ok=True)

    async def start(self, persona: dict) -> str:
        agent_id = persona.get("id")
        if not isinstance(agent_id, str) or not AGENT_ID.match(agent_id):
            raise ValueError("persona id must be 1 to 64 letters, digits, _ or -")
        if agent_id in self.procs and self.procs[agent_id].returncode is None:
            raise ValueError(f"{agent_id} is already running")
        persona_file = self.run_dir / "personas" / f"{agent_id}.json"
        persona_file.write_text(json.dumps(persona, indent=2))
        log_file = open(self.run_dir / "logs" / f"{agent_id}.log", "ab")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", self.module,
            cwd=ROOT,
            env={**os.environ, **self.env, "AGENT_ID": agent_id, "PERSONA_FILE": str(persona_file)},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,  # a Ctrl-C on the world doesn't hit the town
        )
        log_file.close()
        self.procs[agent_id] = proc
        task = asyncio.create_task(self._watch(agent_id, proc))
        self._watchers.add(task)
        task.add_done_callback(self._watchers.discard)
        return agent_id

    async def _watch(self, agent_id: str, proc: asyncio.subprocess.Process) -> None:
        code = await proc.wait()
        try:
            await self.on_exit(agent_id, code)
        except Exception:
            log.exception("exit callback failed for %s", agent_id)

    def running(self) -> set[str]:
        return {agent_id for agent_id, proc in self.procs.items() if proc.returncode is None}

    def kill(self, agent_id: str) -> bool:
        """SIGKILL, like a crash. The agent gets no chance to say goodbye."""
        proc = self.procs.get(agent_id)
        if proc is None or proc.returncode is not None:
            return False
        proc.send_signal(signal.SIGKILL)
        return True

    async def stop_all(self, grace: float = 5.0) -> None:
        """Wait up to `grace` for agents to exit on their own, then terminate, then kill."""
        alive = [p for p in self.procs.values() if p.returncode is None]
        if not alive:
            return
        _, pending = await asyncio.wait([asyncio.create_task(p.wait()) for p in alive], timeout=grace)
        for proc in alive:
            if proc.returncode is None:
                proc.terminate()
        if pending:
            _, pending = await asyncio.wait(pending, timeout=3)
        for proc in alive:
            if proc.returncode is None:
                proc.kill()
        if pending:
            await asyncio.wait(pending, timeout=3)
