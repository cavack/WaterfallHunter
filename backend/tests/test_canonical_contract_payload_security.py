import pytest
from pydantic import ValidationError

from waterfallhunter.core.contracts import NotificationEvent, SignalDecisionPacket
from test_canonical_contracts import _valid_notification_event


@pytest.mark.parametrize(
    "key",
    [
        "apitoken",
        "accesstoken",
        "authtoken",
        "bearertoken",
        "usersecret",
        "tokens",
        "credentials",
    ],
)
def test_notification_event_rejects_concatenated_or_plural_secret_like_keys(key):
    invalid_event = _valid_notification_event(
        payload={"metadata": {key: "must-not-be-accepted"}},
    )

    with pytest.raises(ValidationError):
        NotificationEvent(**invalid_event)

@pytest.mark.parametrize("key", ["deliveryState", "telegramMessageId"])
def test_notification_event_rejects_camel_case_delivery_keys(key):
    invalid_event = _valid_notification_event(
        payload={"metadata": {key: "must-not-be-accepted"}},
    )

    with pytest.raises(ValidationError):
        NotificationEvent(**invalid_event)


def test_canonical_contracts_reject_integers_outside_rfc8785_domain():
    from test_canonical_contracts import _valid_signal_packet

    too_large = 2**53
    invalid_signal = _valid_signal_packet()
    invalid_signal["eligibility_gates"] = {"timestamp": too_large}
    invalid_event = _valid_notification_event(payload={"timestamp": too_large})

    with pytest.raises(ValidationError):
        SignalDecisionPacket(**invalid_signal)
    with pytest.raises(ValidationError):
        NotificationEvent(**invalid_event)
