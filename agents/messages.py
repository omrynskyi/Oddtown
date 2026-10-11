"""STUB owned by Anthony (agent brain). Replace with the real envelope and types.

Only the message type names live here for now, so the platform can route and
tally them. The header fields (msg_id, from, to, sent_at) are filled in by
transport.town.Town on every send.
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
AGENT_HELLO = "agent.hello"
AGENT_LAST_WORDS = "agent.last_words"
WORLD_EVENT = "world.event"
REST = "rest"

# Platform messages to the world service, point to point
SPAWN_REQUEST = "spawn.request"
USAGE_REPORT = "usage.report"
HUMAN_REQUEST = "human.request"


def envelope(type: str, body: dict | None = None) -> dict:
    return {"type": type, "body": body or {}}
