from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[2]
PRODUCT_ROOTS = (
    PROJECT_ROOT / "backend" / "src",
    PROJECT_ROOT / "frontend",
)
FORBIDDEN_PRODUCT_CLAIMS = (
    "tp_24h_probability",
    "_tp_probability",
    "empirical_probability",
    "TP within 24h",
    "TP 24h",
)


def test_product_paths_cannot_reintroduce_misleading_probability_claims() -> None:
    violations: list[str] = []
    for root in PRODUCT_ROOTS:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx"}:
                continue
            text = path.read_text(encoding="utf-8")
            for claim in FORBIDDEN_PRODUCT_CLAIMS:
                if claim in text:
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}: {claim}")
    assert violations == []


def test_evidence_score_is_not_labeled_as_a_probability() -> None:
    ranking_source = (
        PROJECT_ROOT
        / "backend"
        / "src"
        / "waterfallhunter"
        / "core"
        / "final_ranking.py"
    ).read_text(encoding="utf-8")
    assert "signal_score" in ranking_source
    assert "probability" not in ranking_source.lower()
