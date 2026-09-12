from __future__ import annotations

from pathlib import Path
import shutil

from scripts import validate_wfh_skills as validator


REPO = Path(__file__).resolve().parents[2]


def test_v2_inventory_includes_skill_system_curator() -> None:
    assert "skill-system-curator" in validator.EXPECTED_SKILLS
    assert (REPO / "skills/waterfallhunter/skill-system-curator/SKILL.md").is_file()
    assert (REPO / ".agents/skills/skill-system-curator/SKILL.md").is_file()


def test_all_canonical_skills_expose_v2_operating_contract() -> None:
    headings = (
        "## Input Contract",
        "## Required Evidence",
        "## Tool Preference",
        "## Output Contract",
        "## Stop and Escalation Conditions",
    )
    for name in sorted(validator.EXPECTED_SKILLS):
        text = (REPO / "skills/waterfallhunter" / name / "SKILL.md").read_text(encoding="utf-8")
        for heading in headings:
            assert heading in text, f"{name} missing {heading}"


def test_static_validator_rejects_oversized_skill_body(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    text = "# Skill\n" + "line\n" * 501
    errors = validator._validate_body(path, text)
    assert any("500 lines" in error for error in errors)


def _copy_skill_validation_tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(REPO / "skills", root / "skills")
    shutil.copytree(REPO / ".agents" / "skills", root / ".agents" / "skills")
    shutil.copytree(REPO / "docs" / "chatgpt-project", root / "docs" / "chatgpt-project")
    return root


def test_validator_rejects_readme_skill_inventory_drift(tmp_path: Path) -> None:
    root = _copy_skill_validation_tree(tmp_path)
    readme = root / "skills/waterfallhunter/README.md"
    text = readme.read_text(encoding="utf-8")
    text = "\n".join(line for line in text.splitlines() if "`skill-system-curator`" not in line) + "\n"
    readme.write_text(text, encoding="utf-8")
    errors = validator.validate(root)
    assert any("README" in error and "skill-system-curator" in error for error in errors)


def test_validator_rejects_project_catalog_inventory_drift(tmp_path: Path) -> None:
    root = _copy_skill_validation_tree(tmp_path)
    catalog = root / "docs/chatgpt-project/01-WFH-SKILL-CATALOG-v2.md"
    text = catalog.read_text(encoding="utf-8")
    text = "\n".join(line for line in text.splitlines() if "skill-system-curator" not in line) + "\n"
    catalog.write_text(text, encoding="utf-8")
    errors = validator.validate(root)
    assert any("CATALOG" in error and "skill-system-curator" in error for error in errors)


def test_skill_body_line_limit_excludes_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    headings = "\n".join(sorted(validator.REQUIRED_HEADINGS))
    body = "# Skill\n" + headings + "\n" + validator.LIVE_SAFETY_MARKER + "\n"
    body += "filler\n" * (500 - len(body.splitlines()))
    path.write_text("---\nname: test-skill\ndescription: Use when testing body limits.\n---\n" + body, encoding="utf-8")
    errors = validator._validate_skill(path, "test-skill")
    assert not any("500 lines" in error for error in errors)


def test_skill_body_line_limit_rejects_501_body_lines(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    headings = "\n".join(sorted(validator.REQUIRED_HEADINGS))
    body = "# Skill\n" + headings + "\n" + validator.LIVE_SAFETY_MARKER + "\n"
    body += "filler\n" * (501 - len(body.splitlines()))
    path.write_text("---\nname: test-skill\ndescription: Use when testing body limits.\n---\n" + body, encoding="utf-8")
    errors = validator._validate_skill(path, "test-skill")
    assert any("500 lines" in error for error in errors)
