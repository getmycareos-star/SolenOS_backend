import base64
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.services.web_push_config import vapid_configuration_error


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _key_pair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_bytes = private_key.private_numbers().private_value.to_bytes(32, "big")
    public_bytes = private_key.public_key().public_bytes(
        Encoding.X962,
        PublicFormat.UncompressedPoint,
    )
    return _encode(public_bytes), _encode(private_bytes)


def _settings(public_key: str, private_key: str, subject: str = "mailto:support@solenosai.net"):
    return SimpleNamespace(
        vapid_public_key=public_key,
        vapid_private_key=private_key,
        vapid_subject=subject,
    )


def test_vapid_configuration_accepts_matching_p256_key_pair():
    public_key, private_key = _key_pair()

    assert vapid_configuration_error(_settings(public_key, private_key)) is None


def test_vapid_configuration_rejects_mismatched_public_and_private_keys():
    public_key, _ = _key_pair()
    _, private_key = _key_pair()

    assert "do not form a matching pair" in vapid_configuration_error(
        _settings(public_key, private_key)
    )


def test_vapid_configuration_rejects_invalid_key_encodings():
    assert "public key" in vapid_configuration_error(_settings("not-base64!", "also-invalid"))
    assert "private key" in vapid_configuration_error(_settings(*_key_pair()[:1], "bad"))


def test_vapid_configuration_rejects_non_https_and_non_mailto_subjects():
    public_key, private_key = _key_pair()

    assert "subject" in vapid_configuration_error(
        _settings(public_key, private_key, "http://example.com")
    )
    assert "subject" in vapid_configuration_error(
        _settings(public_key, private_key, "https:///missing-host")
    )


def test_vapid_configuration_requires_both_keys():
    assert "both required" in vapid_configuration_error(_settings("", ""))


def test_web_push_worker_fails_closed_for_mismatched_vapid_keys(monkeypatch):
    from app.services import reminder_delivery

    public_key, _ = _key_pair()
    _, private_key = _key_pair()
    settings = _settings(public_key, private_key)
    monkeypatch.setattr(reminder_delivery, "get_settings", lambda: settings)

    # Invalid VAPID configuration must be rejected before opening a DB session
    # or attempting an external provider request.
    monkeypatch.setattr(
        reminder_delivery,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("database must not be opened")),
    )
    assert reminder_delivery.deliver_pending_web_pushes() == 0
