from __future__ import annotations

import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
PHRASE = "ادامه کار گروهی"
PROJECT = "TWFH"
REPOSITORY = "cavack/WFH-ORG"
PROTOCOL = "wfh_mission_continuity_v1"
ROUTE = "mission_continuity"
CANONICAL_MAPPING = (
    "phrase=ادامه کار گروهی | project=TWFH | repository=cavack/WFH-ORG | "
    "protocol=wfh_mission_continuity_v1 | route=mission_continuity"
)


def _read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def test_group_work_resume_phrase_is_cross_surface_and_unambiguous() -> None:
    surfaces = {
        "codex": _read("AGENTS.md"),
        "project_instructions": _read("docs/chatgpt-project/PROJECT-INSTRUCTIONS-v2.txt"),
        "project_router": _read("docs/chatgpt-project/00-WFH-CHATGPT-ROUTER-v2.md"),
        "resume_overlay": _read("docs/chatgpt-project/TWFH-RESUME.md"),
    }

    for name, text in surfaces.items():
        assert CANONICAL_MAPPING in text, name


def test_council_manifest_maps_phrase_to_existing_continuity_route() -> None:
    manifest = json.loads(_read(".agents/wfh-council/manifest.json"))
    role_ids = {role["id"] for role in manifest["roles"]}

    route = manifest["routes"][ROUTE]
    assert route == [
        "chief_orchestrator",
        "capability_scout",
        "skill_system_curator",
        "regression_lead",
    ]
    assert set(route) <= role_ids

    intent = manifest["resume_intents"][PHRASE]
    assert intent == {
        "project": PROJECT,
        "repository": REPOSITORY,
        "protocol": PROTOCOL,
        "route": ROUTE,
        "resolution": "active_mission_latest_certified_checkpoint",
    }


def test_codex_entry_uses_real_resume_cli_contract() -> None:
    text = _read("AGENTS.md")

    assert "python3 scripts/wfh_mission.py resume --intent" in text
    assert "--phrase" not in text
    assert "git status" in text or "worktree" in text.lower()

def test_resume_surfaces_require_authorization_floor_and_structured_preconditions() -> None:
    surfaces = {
        "codex": _read("AGENTS.md"),
        "project_instructions": _read("docs/chatgpt-project/PROJECT-INSTRUCTIONS-v2.txt"),
        "project_router": _read("docs/chatgpt-project/00-WFH-CHATGPT-ROUTER-v2.md"),
        "resume_overlay": _read("docs/chatgpt-project/TWFH-RESUME.md"),
    }

    for name, text in surfaces.items():
        lowered = text.lower()
        assert "minimum authorization" in lowered, name
        assert "structured precondition" in lowered, name
        assert "fail closed" in lowered or "resume_blocked" in lowered, name
