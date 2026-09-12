from waterfallhunter.core.contracts import SignalClass
from waterfallhunter.core.signal_metadata import (
    ClassificationMethod,
    METADATA_CONTRACT_VERSION,
    MODEL_GENERATION,
    STRICT_STRATEGY_PROFILE,
    SignalMetadataInput,
)


def strict_signal_metadata(
    *,
    analysis_observed_at: int,
    reference_observed_at: int | None = None,
) -> SignalMetadataInput:
    """Build explicit STRICT lineage for test-only persisted signals."""

    return SignalMetadataInput(
        signal_class=SignalClass.STRICT,
        strategy_profile=STRICT_STRATEGY_PROFILE,
        score_version="score_v2",
        model_generation=MODEL_GENERATION,
        decision_contract_hash="a" * 64,
        analysis_observed_at=analysis_observed_at,
        reference_observed_at=reference_observed_at,
        metadata_contract_version=METADATA_CONTRACT_VERSION,
        classification_method=ClassificationMethod.FUTURE_PIPELINE_EXPLICIT,
        classification_evidence_hash=None,
    )
