from __future__ import annotations

import hashlib
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waterfallhunter.core.canonical_json import canonical_json_bytes
from waterfallhunter.core.contracts import SignalClass


Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonEmptyStr = Annotated[str, Field(min_length=1)]
NonNegativeTimestamp = Annotated[int, Field(ge=0, strict=True)]

METADATA_CONTRACT_VERSION: Literal["signal_metadata_v1"] = "signal_metadata_v1"
STRICT_STRATEGY_PROFILE = "strict_score_v2"
EXPERIMENTAL_STRATEGY_PROFILE = "experimental_pretrigger_v1"
MODEL_GENERATION = "waterfall_signal_model_v1"
STRICT_SCORE_VERSION = "score_v2"
EXPERIMENTAL_SCORE_VERSION = "score_v2_watch_v1"


class ClassificationMethod(str, Enum):
    FUTURE_PIPELINE_EXPLICIT = "FUTURE_PIPELINE_EXPLICIT"
    LEGACY_PROFILE_EXACT_MATCH = "LEGACY_PROFILE_EXACT_MATCH"


def validate_lineage_pair(
    signal_class: SignalClass,
    strategy_profile: str,
) -> None:
    expected_profile = {
        SignalClass.STRICT: STRICT_STRATEGY_PROFILE,
        SignalClass.EXPERIMENTAL: EXPERIMENTAL_STRATEGY_PROFILE,
    }.get(signal_class)
    if expected_profile != strategy_profile:
        raise ValueError(
            "signal class and strategy profile do not match canonical lineage"
        )


class SignalMetadataInput(BaseModel):
    """Immutable first-class lineage required for a canonical signal."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        protected_namespaces=(),
    )

    signal_class: SignalClass
    strategy_profile: NonEmptyStr
    score_version: NonEmptyStr
    model_generation: NonEmptyStr
    decision_contract_hash: Sha256Hex
    analysis_observed_at: NonNegativeTimestamp
    reference_observed_at: NonNegativeTimestamp | None = None
    metadata_contract_version: Literal["signal_metadata_v1"] = "signal_metadata_v1"
    classification_method: ClassificationMethod
    classification_evidence_hash: Sha256Hex | None = None

    @model_validator(mode="after")
    def _validate_lineage(self) -> "SignalMetadataInput":
        validate_lineage_pair(
            self.signal_class,
            self.strategy_profile,
        )
        expected_score_version = {
            SignalClass.STRICT: STRICT_SCORE_VERSION,
            SignalClass.EXPERIMENTAL: EXPERIMENTAL_SCORE_VERSION,
        }.get(self.signal_class)
        if self.score_version != expected_score_version:
            raise ValueError(
                "signal class and score version do not match canonical lineage"
            )
        if (
            self.classification_method
            is ClassificationMethod.FUTURE_PIPELINE_EXPLICIT
            and self.classification_evidence_hash is not None
        ):
            raise ValueError(
                "future explicit lineage must not carry legacy evidence hash"
            )
        if (
            self.classification_method
            is ClassificationMethod.LEGACY_PROFILE_EXACT_MATCH
            and self.classification_evidence_hash is None
        ):
            raise ValueError(
                "legacy classification requires classification evidence hash"
            )
        return self


def _required_timestamp(metrics: dict[str, Any], field: str) -> int:
    value = metrics.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be an explicit non-negative integer timestamp")
    return value


def _optional_timestamp(metrics: dict[str, Any], field: str) -> int | None:
    value = metrics.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be null or a non-negative integer timestamp")
    return value


def build_signal_metadata_input(
    metrics: dict[str, Any],
    decision_contract_hash: str,
) -> SignalMetadataInput:
    """Build explicit future lineage without defaults or wall-clock inference."""

    if not isinstance(metrics, dict):
        raise ValueError("signal metadata metrics must be a mapping")

    strategy_profile = metrics.get("strategy_profile")
    if not isinstance(strategy_profile, str):
        raise ValueError("strategy_profile must be an exact recognized future profile")

    score_version = metrics.get("score_version")
    lineage = {
        STRICT_STRATEGY_PROFILE: (SignalClass.STRICT, STRICT_SCORE_VERSION),
        EXPERIMENTAL_STRATEGY_PROFILE: (
            SignalClass.EXPERIMENTAL,
            EXPERIMENTAL_SCORE_VERSION,
        ),
    }.get(strategy_profile)
    if lineage is None:
        raise ValueError("strategy_profile must be an exact recognized future profile")

    signal_class, expected_score_version = lineage
    if score_version != expected_score_version:
        raise ValueError("score_version does not match the explicit strategy profile")

    return SignalMetadataInput(
        signal_class=signal_class,
        strategy_profile=strategy_profile,
        score_version=score_version,
        model_generation=MODEL_GENERATION,
        decision_contract_hash=decision_contract_hash,
        analysis_observed_at=_required_timestamp(metrics, "analysis_observed_at"),
        reference_observed_at=_optional_timestamp(
            metrics,
            "reference_observed_at",
        ),
        metadata_contract_version=METADATA_CONTRACT_VERSION,
        classification_method=ClassificationMethod.FUTURE_PIPELINE_EXPLICIT,
        classification_evidence_hash=None,
    )


def canonical_sha256(value: Any) -> str:
    """Return SHA-256 over RFC8785/JCS canonical bytes."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
