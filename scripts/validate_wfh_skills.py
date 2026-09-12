from __future__ import annotations

import re
import sys
from pathlib import Path

EXPECTED_SKILLS = {
    "engineering-orchestrator",
    "repository-architecture-auditor",
    "runtime-reliability-performance",
    "backend-data-architecture",
    "api-contract-schema-guardian",
    "frontend-dashboard-ux",
    "strategy-score-lifecycle",
    "scientific-backtest-validation",
    "market-data-evidence-quality",
    "verification-regression",
    "security-supply-chain",
    "skill-system-curator",
    "observability-incident-response",
    "release-production-certification",
}

REQUIRED_HEADINGS = {
    "## Overview",
    "## When to Use",
    "## Scope",
    "## Protected Invariants",
    "## Workflow",
    "## Evidence and Readiness",
    "## Verification",
    "## Handoffs",
    "## Common Mistakes",
    "## Input Contract",
    "## Required Evidence",
    "## Tool Preference",
    "## Output Contract",
    "## Stop and Escalation Conditions",
}

REQUIRED_SHARED_SECTIONS = {
    "## Discovery adapters",
    "## Shared evidence taxonomy",
    "## Freshness rule",
    "## Protected invariants",
    "## Stop and escalation conditions",
    "## Correct invocation and failure examples",
    "## External tools and plugins",
    "## Safety boundary",
}

NAME_RE = re.compile(r"^[a-z0-9-]+$")
README_SKILL_ROW_RE = re.compile(r"^\|\s*`([a-z0-9-]+)`\s*\|", re.MULTILINE)
CATALOG_SKILL_ROW_RE = re.compile(
    r"^\|\s*\d+\s*\|\s*([a-z0-9-]+)\s*\|\s*`([^`]+)`\s*\|",
    re.MULTILINE,
)
PLACEHOLDER_RE = re.compile(
    r"\b(?:TBD|TODO|FIXME)\b|implement later|fill in details",
    flags=re.IGNORECASE,
)
ALLOWED_FRONTMATTER_KEYS = {"name", "description"}
LIVE_SAFETY_MARKER = "Live order placement is outside this skill system"
ADAPTER_MARKER = "contains no independent workflow"


def _read_text(path: Path) -> tuple[str | None, list[str]]:
    try:
        return path.read_text(encoding="utf-8"), []
    except OSError as exc:
        return None, [f"{path}: unable to read: {exc}"]


def _frontmatter_bounds(lines: list[str], path: Path) -> tuple[int | None, list[str]]:
    if not lines or lines[0].strip() != "---":
        return None, [f"{path}: missing opening YAML frontmatter delimiter"]
    try:
        return next(i for i in range(1, len(lines)) if lines[i].strip() == "---"), []
    except StopIteration:
        return None, [f"{path}: missing closing YAML frontmatter delimiter"]


def _parse_frontmatter_line(
    stripped: str, path: Path, values: dict[str, str]
) -> list[str]:
    if ":" not in stripped:
        return [f"{path}: invalid frontmatter line: {stripped!r}"]

    key, raw_value = stripped.split(":", 1)
    key = key.strip()
    value = raw_value.strip()
    if key not in ALLOWED_FRONTMATTER_KEYS:
        return [f"{path}: unsupported frontmatter field {key!r}"]
    if key in values:
        return [f"{path}: duplicate frontmatter field {key!r}"]
    if not value:
        return [f"{path}: frontmatter field {key!r} must not be empty"]
    if value[0] in {'\"', "'"} or value[-1] in {'\"', "'"}:
        return [
            f"{path}: quoted frontmatter values are not supported; use a plain scalar for {key!r}"
        ]
    if value.startswith(("[", "{", "|", ">", "&", "*", "!")):
        return [f"{path}: unsupported YAML syntax in frontmatter field {key!r}"]

    values[key] = value
    return []


def _parse_frontmatter(text: str, path: Path) -> tuple[dict[str, str], list[str]]:
    lines = text.splitlines()
    closing, errors = _frontmatter_bounds(lines, path)
    if closing is None:
        return {}, errors

    raw = "\n".join(lines[: closing + 1])
    if len(raw) > 1024:
        errors.append(f"{path}: frontmatter exceeds 1024 characters")

    values: dict[str, str] = {}
    for line in lines[1:closing]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        errors.extend(_parse_frontmatter_line(stripped, path, values))
    return values, errors


def _validate_name(path: Path, expected_name: str, name: str | None) -> list[str]:
    if not name:
        return [f"{path}: frontmatter field 'name' is required"]

    errors: list[str] = []
    if name != expected_name:
        errors.append(
            f"{path}: frontmatter name {name!r} must equal directory {expected_name!r}"
        )
    if not NAME_RE.fullmatch(name):
        errors.append(f"{path}: name must match ^[a-z0-9-]+$")
    return errors


def _validate_description(path: Path, description: str | None) -> list[str]:
    if not description:
        return [f"{path}: frontmatter field 'description' is required"]

    errors: list[str] = []
    if not description.startswith("Use when"):
        errors.append(f"{path}: description must start with 'Use when'")
    if len(description) > 500:
        errors.append(f"{path}: description exceeds 500 characters")
    return errors


def _validate_body(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    if len(text.splitlines()) > 500:
        errors.append(f"{path}: skill body exceeds 500 lines")
    if not any(line.startswith("# ") for line in text.splitlines()):
        errors.append(f"{path}: missing top-level '# ' title")
    for heading in sorted(REQUIRED_HEADINGS):
        if heading not in text:
            errors.append(f"{path}: missing required heading {heading!r}")
    match = PLACEHOLDER_RE.search(text)
    if match:
        errors.append(f"{path}: contains placeholder text {match.group(0)!r}")
    if LIVE_SAFETY_MARKER not in text:
        errors.append(f"{path}: missing categorical live-order safety boundary")
    return errors


def _validate_skill(path: Path, expected_name: str) -> list[str]:
    text, errors = _read_text(path)
    if text is None:
        return errors

    frontmatter, frontmatter_errors = _parse_frontmatter(text, path)
    errors.extend(frontmatter_errors)
    errors.extend(_validate_name(path, expected_name, frontmatter.get("name")))
    errors.extend(_validate_description(path, frontmatter.get("description")))
    lines = text.splitlines()
    closing, _ = _frontmatter_bounds(lines, path)
    body = "\n".join(lines[closing + 1 :]) if closing is not None else text
    errors.extend(_validate_body(path, body))
    return errors


def _validate_shared_readme(path: Path) -> list[str]:
    text, errors = _read_text(path)
    if text is None:
        return errors
    for heading in sorted(REQUIRED_SHARED_SECTIONS):
        if heading not in text:
            errors.append(f"{path}: missing shared contract section {heading!r}")
    if ".agents/skills/" not in text:
        errors.append(f"{path}: missing discovery-adapter path documentation")
    if "must not authorize, design, implement, or enable live order placement" not in text:
        errors.append(f"{path}: missing categorical live-order safety policy")
    return errors


def _inventory_drift_errors(path: Path, label: str, names: list[str]) -> list[str]:
    found = set(names)
    errors = [
        f"{path}: {label} missing canonical skill {name}"
        for name in sorted(EXPECTED_SKILLS - found)
    ]
    errors.extend(
        f"{path}: {label} contains unexpected skill {name}"
        for name in sorted(found - EXPECTED_SKILLS)
    )
    duplicates = sorted({name for name in names if names.count(name) > 1})
    errors.extend(f"{path}: {label} duplicates skill {name}" for name in duplicates)
    return errors


def _validate_readme_inventory(path: Path) -> list[str]:
    text, errors = _read_text(path)
    if text is None:
        return errors
    names = README_SKILL_ROW_RE.findall(text)
    return [*errors, *_inventory_drift_errors(path, "README inventory", names)]


def _validate_project_catalog(path: Path) -> list[str]:
    text, errors = _read_text(path)
    if text is None:
        return errors
    rows = CATALOG_SKILL_ROW_RE.findall(text)
    names = [name for name, _ in rows]
    errors.extend(_inventory_drift_errors(path, "CATALOG inventory", names))
    for name, canonical_path in rows:
        expected = f"skills/waterfallhunter/{name}/SKILL.md"
        if canonical_path != expected:
            errors.append(
                f"{path}: CATALOG skill {name} path {canonical_path!r} must equal {expected!r}"
            )
    return errors


def _validate_public_skill_catalogs(root: Path, readme: Path) -> list[str]:
    catalog = root / "docs" / "chatgpt-project" / "01-WFH-SKILL-CATALOG-v2.md"
    errors = _validate_readme_inventory(readme)
    if not catalog.is_file():
        errors.append(f"{catalog}: missing Project Source skill CATALOG")
    else:
        errors.extend(_validate_project_catalog(catalog))
    return errors


def _expected_adapter_body(expected_name: str) -> str:
    canonical = f"../../../skills/waterfallhunter/{expected_name}/SKILL.md"
    return "\n".join(
        [
            "# WaterfallHunter discovery adapter",
            "",
            "Before acting:",
            "1. Read `../../../skills/waterfallhunter/README.md`.",
            f"2. Read `{canonical}`.",
            "3. Treat the canonical file as authoritative; this adapter contains no independent workflow.",
            "4. If either file cannot be loaded, stop and report the missing repository context.",
        ]
    )


def _adapter_body(text: str, path: Path) -> str | None:
    lines = text.splitlines()
    closing, _ = _frontmatter_bounds(lines, path)
    if closing is None:
        return None
    return "\n".join(lines[closing + 1 :]).strip()


def _validate_adapter(path: Path, expected_name: str) -> list[str]:
    text, errors = _read_text(path)
    if text is None:
        return errors
    frontmatter, frontmatter_errors = _parse_frontmatter(text, path)
    errors.extend(frontmatter_errors)
    errors.extend(_validate_name(path, expected_name, frontmatter.get("name")))
    errors.extend(_validate_description(path, frontmatter.get("description")))

    body = _adapter_body(text, path)
    if body is not None and body != _expected_adapter_body(expected_name):
        errors.append(
            f"{path}: discovery adapter body must match canonical delegation template exactly"
        )
    return errors


def _validate_skill_directories(skill_root: Path) -> list[str]:
    if not skill_root.is_dir():
        return [f"{skill_root}: missing skill root"]
    actual_dirs = {
        path.name
        for path in skill_root.iterdir()
        if path.is_dir() and path.name != "tests"
    }
    return [
        f"{skill_root / name}: unexpected skill directory"
        for name in sorted(actual_dirs - EXPECTED_SKILLS)
    ]


def validate(root: Path) -> list[str]:
    root = root.resolve()
    skill_root = root / "skills" / "waterfallhunter"
    adapter_root = root / ".agents" / "skills"
    errors = _validate_skill_directories(skill_root)

    readme = skill_root / "README.md"
    if not readme.is_file():
        errors.append(f"{readme}: missing system README")
    else:
        errors.extend(_validate_shared_readme(readme))
        errors.extend(_validate_public_skill_catalogs(root, readme))

    for name in sorted(EXPECTED_SKILLS):
        skill_file = skill_root / name / "SKILL.md"
        if not skill_file.is_file():
            errors.append(f"{skill_file}: missing skill file")
        else:
            errors.extend(_validate_skill(skill_file, name))

        adapter_file = adapter_root / name / "SKILL.md"
        if not adapter_file.is_file():
            errors.append(f"{adapter_file}: missing discovery adapter")
        else:
            errors.extend(_validate_adapter(adapter_file, name))

    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = validate(root)
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
