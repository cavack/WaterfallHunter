import pytest
from pydantic import ValidationError

from waterfallhunter.core.contracts import SignalClass
from waterfallhunter.core.decision_provenance import decision_contract_sha256
from waterfallhunter.core.signal_metadata import (
    ClassificationMethod,
    EXPERIMENTAL_SCORE_VERSION,
    EXPERIMENTAL_STRATEGY_PROFILE,
    METADATA_CONTRACT_VERSION,
    MODEL_GENERATION,
    STRICT_STRATEGY_PROFILE,
    SignalMetadataInput,
    canonical_sha256,
)


def _metadata(**overrides) -> SignalMetadataInput:
    values = {
        "signal_class": SignalClass.STRICT,
        "strategy_profile": STRICT_STRATEGY_PROFILE,
        "score_version": "score_v2",
        "model_generation": MODEL_GENERATION,
        "decision_contract_hash": "a" * 64,
        "analysis_observed_at": 1_700_000_000,
        "reference_observed_at": 1_699_999_990,
        "metadata_contract_version": METADATA_CONTRACT_VERSION,
        "classification_method": ClassificationMethod.FUTURE_PIPELINE_EXPLICIT,
        "classification_evidence_hash": None,
    }
    values.update(overrides)
    return SignalMetadataInput(**values)


def test_strict_and_experimental_profiles_are_explicit() -> None:
    strict = _metadata()
    assert strict.signal_class is SignalClass.STRICT
    assert strict.strategy_profile == STRICT_STRATEGY_PROFILE

    experimental = _metadata(
        signal_class=SignalClass.EXPERIMENTAL,
        strategy_profile=EXPERIMENTAL_STRATEGY_PROFILE,
        score_version=EXPERIMENTAL_SCORE_VERSION,
    )
    assert experimental.signal_class is SignalClass.EXPERIMENTAL
    assert experimental.strategy_profile == EXPERIMENTAL_STRATEGY_PROFILE
    assert experimental.score_version == "score_v2_watch_v1"


@pytest.mark.parametrize(
    ("signal_class", "profile"),
    [
        (SignalClass.STRICT, EXPERIMENTAL_STRATEGY_PROFILE),
        (SignalClass.EXPERIMENTAL, STRICT_STRATEGY_PROFILE),
        (SignalClass.STRICT, ""),
    ],
)
def test_invalid_lineage_pairs_fail_closed(
    signal_class: SignalClass,
    profile: str,
) -> None:
    with pytest.raises(ValidationError):
        _metadata(signal_class=signal_class, strategy_profile=profile)


@pytest.mark.parametrize(
    ("signal_class", "strategy_profile", "score_version"),
    [
        (SignalClass.STRICT, STRICT_STRATEGY_PROFILE, "score_v2_watch_v1"),
        (
            SignalClass.EXPERIMENTAL,
            EXPERIMENTAL_STRATEGY_PROFILE,
            "score_v2",
        ),
        (SignalClass.STRICT, STRICT_STRATEGY_PROFILE, "unknown"),
    ],
)
def test_invalid_score_version_lineage_fails_closed(
    signal_class: SignalClass,
    strategy_profile: str,
    score_version: str,
) -> None:
    with pytest.raises(ValidationError):
        _metadata(
            signal_class=signal_class,
            strategy_profile=strategy_profile,
            score_version=score_version,
        )


@pytest.mark.parametrize(
    "field",
    ["analysis_observed_at", "reference_observed_at"],
)
def test_timestamp_fields_reject_boolean_values(field: str) -> None:
    with pytest.raises(ValidationError):
        _metadata(**{field: True})


def test_future_lineage_rejects_legacy_evidence_hash() -> None:
    with pytest.raises(ValidationError):
        _metadata(classification_evidence_hash="b" * 64)


def test_legacy_lineage_requires_evidence_hash() -> None:
    with pytest.raises(ValidationError):
        _metadata(
            classification_method=ClassificationMethod.LEGACY_PROFILE_EXACT_MATCH,
            classification_evidence_hash=None,
        )


def test_legacy_lineage_accepts_deterministic_evidence_hash() -> None:
    metadata = _metadata(
        classification_method=ClassificationMethod.LEGACY_PROFILE_EXACT_MATCH,
        classification_evidence_hash="b" * 64,
    )
    assert metadata.classification_evidence_hash == "b" * 64


def test_hashes_are_rfc8785_deterministic() -> None:
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256(
        {"a": 1, "b": 2}
    )
    assert decision_contract_sha256({"b": 2, "a": 1}) == canonical_sha256(
        {"a": 1, "b": 2}
    )


def test_hashing_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError):
        canonical_sha256({"bad": float("nan")})


def test_hash_fields_must_be_lowercase_sha256_hex() -> None:
    with pytest.raises(ValidationError):
        _metadata(decision_contract_hash="A" * 64)
    with pytest.raises(ValidationError):
        _metadata(decision_contract_hash="a" * 63)
