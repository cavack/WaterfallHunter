"""Versioned feature and threshold registry for Lifecycle V2 shadow."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waterfallhunter.core.signal_metadata import canonical_sha256


class LifecycleProfile(str, Enum):
    STRICT = "STRICT_RETEST_BREAKDOWN_SHORT_V1"
    EXPERIMENTAL = "EXPERIMENTAL_EARLY_DECAY_V1"


class FeatureDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    family: Literal[
        "FUEL", "STRUCTURE", "FLOW", "RELATIVE", "ANTI_CHASE", "TRIGGER", "EXECUTION", "PORTFOLIO"
    ]
    source_path: str = Field(min_length=1)
    allowed_model_stage: Literal["DISCOVERY", "SETUP", "TRIGGER", "EXECUTION"]
    leakage_risk: Literal["LOW", "MEDIUM", "HIGH"]
    provenance_required: bool = True


FEATURE_DEFINITIONS = (
    FeatureDefinition(name="fuel_rich", family="FUEL", source_path="stage_lifecycle.confirmed.hype", allowed_model_stage="DISCOVERY", leakage_risk="LOW"),
    FeatureDefinition(name="structure_count", family="STRUCTURE", source_path="candle_features.*.(lower_high|setup)", allowed_model_stage="SETUP", leakage_risk="LOW"),
    FeatureDefinition(name="flow_family_pass", family="FLOW", source_path="microstructure.footprint.aggressive_selling", allowed_model_stage="SETUP", leakage_risk="MEDIUM"),
    FeatureDefinition(name="relative_family_pass", family="RELATIVE", source_path="relative_weakness_features.timeframes.*", allowed_model_stage="SETUP", leakage_risk="LOW"),
    FeatureDefinition(name="anti_chase_pass", family="ANTI_CHASE", source_path="candle_features.*.extension_from_support_atr", allowed_model_stage="SETUP", leakage_risk="LOW"),
    FeatureDefinition(name="lower_tf_trigger_closed", family="TRIGGER", source_path="breakdown_confirmation.composite_breakdown_confirmed", allowed_model_stage="TRIGGER", leakage_risk="HIGH"),
    FeatureDefinition(name="distance_to_trigger_atr", family="ANTI_CHASE", source_path="candle_features.*.distance_to_support_atr", allowed_model_stage="SETUP", leakage_risk="LOW"),
    FeatureDefinition(name="lbank_constraints_fresh", family="EXECUTION", source_path="validated_lbank_constraints", allowed_model_stage="EXECUTION", leakage_risk="LOW"),
    FeatureDefinition(name="orderbook_fresh", family="EXECUTION", source_path="microstructure.observed_at", allowed_model_stage="EXECUTION", leakage_risk="MEDIUM"),
    FeatureDefinition(name="levels_constructible", family="EXECUTION", source_path="execution_preview.status", allowed_model_stage="EXECUTION", leakage_risk="LOW"),
    FeatureDefinition(name="estimated_round_trip_cost_r", family="EXECUTION", source_path="execution_preview.estimated_round_trip_cost_r", allowed_model_stage="EXECUTION", leakage_risk="MEDIUM"),
    FeatureDefinition(name="executable_depth_multiple", family="EXECUTION", source_path="execution_preview.executable_depth_multiple", allowed_model_stage="EXECUTION", leakage_risk="MEDIUM"),
    FeatureDefinition(name="preliminary_portfolio_capacity", family="PORTFOLIO", source_path="portfolio_capacity.available", allowed_model_stage="EXECUTION", leakage_risk="LOW"),
    FeatureDefinition(name="confirmation_count", family="TRIGGER", source_path="breakdown_confirmation+microstructure.footprint", allowed_model_stage="TRIGGER", leakage_risk="HIGH"),
    FeatureDefinition(name="confirmation_family_count", family="TRIGGER", source_path="breakdown_confirmation+microstructure.footprint", allowed_model_stage="TRIGGER", leakage_risk="HIGH"),
    FeatureDefinition(name="extension_atr", family="ANTI_CHASE", source_path="candle_features.*.extension_from_support_atr", allowed_model_stage="SETUP", leakage_risk="LOW"),
)


def lifecycle_feature_registry() -> dict:
    material = {
        "contract_version": "lifecycle_feature_registry_v1",
        "features": [item.model_dump(mode="json") for item in FEATURE_DEFINITIONS],
        "profiles": [item.value for item in LifecycleProfile],
        "outcome_fields_allowed_as_features": False,
    }
    return {**material, "registry_hash": canonical_sha256(material)}


class LifecycleV2Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    controlled_momentum_max_atr: float = 0.5
    late_atr: float = 0.8
    hard_block_atr: float = 1.0
    structure_count_minimum: int = 2
    estimated_cost_r_maximum: float = 0.15
    executable_depth_multiple_minimum: float = 10.0
    confirmation_count_minimum: int = 2
    confirmation_family_count_minimum: int = 2


class LifecycleV2Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["lifecycle_v2_policy_contract_v1"]
    policy_version: Literal["lifecycle_v2_shadow_policy_v1"]
    threshold_status: Literal["SHADOW_HYPOTHESES"]
    primary_profile: Literal["STRICT_RETEST_BREAKDOWN_SHORT_V1"]
    experimental_profile: Literal["EXPERIMENTAL_EARLY_DECAY_V1"]
    experimental_trigger_allowed: Literal[False]
    thresholds: LifecycleV2Thresholds
    feature_registry_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _hash_matches(self) -> "LifecycleV2Policy":
        material = self.model_dump(mode="json", exclude={"policy_hash"})
        if self.policy_hash != canonical_sha256(material):
            raise ValueError("Lifecycle V2 policy hash mismatch")
        return self


def lifecycle_v2_policy_v1() -> LifecycleV2Policy:
    registry = lifecycle_feature_registry()
    material = {
        "contract_version": "lifecycle_v2_policy_contract_v1",
        "policy_version": "lifecycle_v2_shadow_policy_v1",
        "threshold_status": "SHADOW_HYPOTHESES",
        "primary_profile": LifecycleProfile.STRICT.value,
        "experimental_profile": LifecycleProfile.EXPERIMENTAL.value,
        "experimental_trigger_allowed": False,
        "thresholds": LifecycleV2Thresholds().model_dump(mode="json"),
        "feature_registry_hash": registry["registry_hash"],
    }
    return LifecycleV2Policy.model_validate(
        {**material, "policy_hash": canonical_sha256(material)}
    )
