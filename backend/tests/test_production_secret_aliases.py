from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-production.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_production_workflow_has_no_ssh_secret_aliases() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    ci_text = CI_WORKFLOW.read_text(encoding="utf-8")
    forbidden = (
        "WFH_PROD_HOST",
        "WFH_DEPLOY_HOST",
        "WFH_PROD_PORT",
        "WFH_DEPLOY_PORT",
        "WFH_PROD_USER",
        "WFH_DEPLOY_USER",
        "WFH_PROD_SSH_KEY",
        "WFH_DEPLOY_SSH_KEY",
        "WFH_PROD_KNOWN_HOSTS",
        "WFH_DEPLOY_KNOWN_HOSTS",
    )
    for name in forbidden:
        assert name not in text
        assert name not in ci_text

    assert "wfh-production-${{ github.run_id }}-${{ github.run_attempt }}" in text
    assert "sudo /usr/local/sbin/wfh-production-deploy" in text
