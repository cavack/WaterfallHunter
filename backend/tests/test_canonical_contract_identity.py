import pytest
from pydantic import ValidationError

from waterfallhunter.core.contracts import ExecutionPlan, SignalDecisionPacket
from test_canonical_contracts import _valid_execution_plan, _valid_signal_packet


def test_signal_decision_rejects_wrong_contract_type_or_version():
    wrong_type = {
        **_valid_signal_packet(),
        "contract_type": "execution_plan",
    }
    wrong_version = {
        **_valid_signal_packet(),
        "contract_version": "9.9",
    }

    with pytest.raises(ValidationError):
        SignalDecisionPacket(**wrong_type)
    with pytest.raises(ValidationError):
        SignalDecisionPacket(**wrong_version)


def test_execution_plan_rejects_wrong_contract_type_or_version():
    wrong_type = {
        **_valid_execution_plan(),
        "contract_type": "signal_decision",
    }
    wrong_version = {
        **_valid_execution_plan(),
        "contract_version": "9.9",
    }

    with pytest.raises(ValidationError):
        ExecutionPlan(**wrong_type)
    with pytest.raises(ValidationError):
        ExecutionPlan(**wrong_version)
