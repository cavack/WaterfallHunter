from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NOTIFIER = ROOT / "backend" / "src" / "waterfallhunter" / "core" / "notifier.py"
DASHBOARD = ROOT / "frontend" / "app" / "page.tsx"
DEPLOY_PLAN = ROOT / "docs" / "superpowers" / "plans" / "2026-08-27-secure-github-production-deploy.md"
DEPLOY_SPEC = ROOT / "docs" / "superpowers" / "specs" / "2026-08-27-secure-github-production-deploy-design.md"


def test_runtime_alert_and_dashboard_status_use_signal_only_wording() -> None:
    notifier = NOTIFIER.read_text(encoding="utf-8")
    dashboard = DASHBOARD.read_text(encoding="utf-8")

    assert "WATERFALL SIGNAL — SIGNAL_ONLY ALERT" in notifier
    assert "WATERFALL SIGNAL — SIMULATED ALERT" not in notifier
    assert "SIGNAL_ONLY · LIVE TRADING OFF" in dashboard
    assert "SIMULATED ONLY · LIVE TRADING OFF" not in dashboard


def test_current_deployment_docs_keep_signal_only_as_canonical_contract() -> None:
    plan = DEPLOY_PLAN.read_text(encoding="utf-8")
    spec = DEPLOY_SPEC.read_text(encoding="utf-8")

    assert 'ExecutionMode.SIGNAL_ONLY = "SIGNAL_ONLY"' in plan
    assert "Current runtime/product contracts emit `SIGNAL_ONLY`." in spec
    assert 'and no product/runtime API response emits `SIGNAL_ONLY`' not in plan
    assert 'deprecated terms (`SIGNAL_ONLY`' not in plan
    assert "terminology is replaced by `SIGNAL_ONLY` / `signal-only`" not in spec
    assert "Any API/contract field currently emitting `SIGNAL_ONLY` must emit `SIGNAL_ONLY`" not in spec
