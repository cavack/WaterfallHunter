import json
from types import SimpleNamespace

from waterfallhunter.core.decision_provenance import (
    build_decision_contract,
    source_tree_sha256,
)
from waterfallhunter.core.multi_exchange_validator import MultiExchangeValidator


def test_source_tree_fingerprint_is_deterministic_and_content_sensitive(tmp_path):
    (tmp_path / "a.py").write_text("value = 1\n")
    first = source_tree_sha256(str(tmp_path))
    second = source_tree_sha256(str(tmp_path))
    assert first == second
    assert first[1] == 1

    (tmp_path / "a.py").write_text("value = 2\n")
    source_tree_sha256.cache_clear()
    assert source_tree_sha256(str(tmp_path))[0] != first[0]


def test_decision_contract_captures_effective_non_secret_configuration():
    validator = MultiExchangeValidator()
    settings = SimpleNamespace(
        environment="production",
        live_trading_enabled=False,
        coinglass_api_key="must-not-appear",
        coinglass_base_url="https://example.invalid",
        dexscreener_enabled=True,
        dexscreener_token_map_json='{"token":"mapping"}',
        onchain_large_transfer_usd=125_000.0,
    )
    contract = build_decision_contract(
        app_version="1.2.3",
        validator=validator,
        settings=settings,
        recorder_bucket_seconds=900,
    )
    encoded = json.dumps(contract, sort_keys=True)

    assert contract["application"]["source_tree_sha256"]
    assert contract["strategy"]["score_version"] == "score_v2"
    assert contract["strategy"]["experimental_profile"] == "experimental_pretrigger_v1"
    assert contract["strategy"]["experimental_pretrigger_enabled"] is False
    assert contract["strategy"]["experimental_pretrigger_threshold"] == 45.0
    assert contract["microstructure"]["executable_notional"] == 50.0
    assert contract["recorder"]["bucket_seconds"] == 900
    assert contract["runtime_settings"]["coinglass_configured"] is True
    assert "must-not-appear" not in encoded
    assert '"token":"mapping"' not in encoded
