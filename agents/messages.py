"""Message types and body shapes for everything characters send each other.

The header fields (msg_id, from, to, sent_at) are filled in by
transport.town.Town on every send, so agents only build `{"type", "body"}`.

Bodies, by type. A `branch` is always the full branch dict from agents.schema.Branch.

    idea.share       {"branch", "note"}
    idea.reaction    {"branch_id", "answer": "accept"|"reject", "text"}
    idea.pitch       {"branch", "note"}
    pitch.response   {"branch_id", "persuaded": bool, "text"}
    merge.propose    {"proposal_id", "branch_ids": [a, b], "text", "note"}
    merge.response   {"proposal_id", "answer": "accept"|"reject"|"counter", "text", "branch"?}
    replace.request  {"replaces", "replaces_name", "role": "propose"|"refine", "order": [ids], "generation", "briefing"}
    persona.draft    {"replaces", "replaces_name", "order", "step", "generation", "draft", "changes", "briefing"}
    endorse          {"branch", "reason"}
    unendorse        {"branch_id", "reason"}
    objection        {"branch_id", "reason"}
    agent.hello      {"name", "one_liner", "replaces"}
    agent.last_words {"text"}
    rest             {}
    world.event      {"kind": "seed"|"constraint"|"feedback"|"human.answer"|"stop", "text", ...}

To the world service: spawn.request {"persona"}, usage.report {"tokens", ...},
human.request {"kind", ...}. From it: spawn.result {"ok", "agent_id"|"error"}.
"""

# Point to point
IDEA_SHARE = "idea.share"
IDEA_REACTION = "idea.reaction"
IDEA_PITCH = "idea.pitch"
PITCH_RESPONSE = "pitch.response"
MERGE_PROPOSE = "merge.propose"
MERGE_RESPONSE = "merge.response"
REPLACE_REQUEST = "replace.request"
PERSONA_DRAFT = "persona.draft"

# Broadcast
ENDORSE = "endorse"
UNENDORSE = "unendorse"
OBJECTION = "objection"
AGENT_HELLO = "agent.hello"
AGENT_LAST_WORDS = "agent.last_words"
WORLD_EVENT = "world.event"
REST = "rest"

# Platform messages to and from the world service, point to point
SPAWN_REQUEST = "spawn.request"
SPAWN_RESULT = "spawn.result"
USAGE_REPORT = "usage.report"
HUMAN_REQUEST = "human.request"

# Only these wake a character up. Everything else lands in memory for its next call.
WAKING = {IDEA_SHARE, IDEA_PITCH, MERGE_PROPOSE, REPLACE_REQUEST, PERSONA_DRAFT, WORLD_EVENT}

# Waking messages the decision call answers, and the reply type for each.
REPLY_TYPE = {IDEA_SHARE: IDEA_REACTION, IDEA_PITCH: PITCH_RESPONSE, MERGE_PROPOSE: MERGE_RESPONSE}


def envelope(type: str, body: dict | None = None) -> dict:
    return {"type": type, "body": body or {}}
