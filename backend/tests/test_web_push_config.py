import base64
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from app.services.web_push_config import vapid_configuration_error


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _key_pair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_bytes = private_key.private_numbers().private_value.to_bytes(32, "big")
    public_bytes = private_key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return _encode(public_bytes), _encode(private_bytes)


def _settings(public_key: str, private_key: str, subject: str = "mailto:support@solenosai.net"):
    return SimpleNamespace(vapid_public_key=public_key, vapid_private_key=private_key, vapid_subject=subject)


def test_vapid_configuration_accepts_matching_p256_key_pair():
    public_key, private_key = _key_pair()
    assert vapid_configuration_error(_settings(public_key, private_key)) is None


def test_vapid_configuration_accepts_pem_private_key_with_escaped_newlines():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_bytes = private_key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    private_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii")
    assert vapid_configuration_error(_settings(_encode(public_bytes), private_pem.replace("\n", "\\n"))) is None


def test_vapid_configuration_rejects_mismatched_keys():
    public_key, _ = _key_pair()
    _, private_key = _key_pair()
    assert "do not form a matching pair" in vapid_configuration_error(_settings(public_key, private_key))


def test_vapid_configuration_rejects_malformed_keys_and_subjects():
    public_key, private_key = _key_pair()
    assert "public key" in vapid_configuration_error(_settings("not-base64!", private_key))
    assert "private key" in vapid_configuration_error(_settings(public_key, "bad"))
    assert "subject" in vapid_configuration_error(_settings(public_key, private_key, "http://example.com"))
    assert "subject" in vapid_configuration_error(_settings(public_key, private_key, "https:///missing-host"))
    assert "subject" in vapid_configuration_error(_settings(public_key, private_key, "https://[malformed"))
    assert vapid_configuration_error(_settings(public_key, private_key, "https://solenosai.net/vapid")) is None


def test_vapid_configuration_requires_both_keys():
    assert "both required" in vapid_configuration_error(_settings("", ""))


def test_worker_rejects_mismatched_keys_before_database_access(monkeypatch):
    from app.services import reminder_delivery
    public_key, _ = _key_pair()
    _, private_key = _key_pair()
    monkeypatch.setattr(reminder_delivery, "get_settings", lambda: _settings(public_key, private_key))
    monkeypatch.setattr(
        reminder_delivery,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("database must not be opened")),
    )
    assert reminder_delivery.deliver_pending_web_pushes() == 0
