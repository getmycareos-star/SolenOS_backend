import base64
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from app.services.web_push_config import vapid_configuration_error


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _key_pair():
    private = ec.generate_private_key(ec.SECP256R1())
    scalar = private.private_numbers().private_value.to_bytes(32, "big")
    public = private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return _encode(public), _encode(scalar)


def _settings(public, private, subject="mailto:support@solenosai.net"):
    return SimpleNamespace(vapid_public_key=public, vapid_private_key=private, vapid_subject=subject)


def test_vapid_pair_validation_accepts_matching_pair_and_rejects_mismatch():
    public, private = _key_pair()
    assert vapid_configuration_error(_settings(public, private)) is None
    other_public, _ = _key_pair()
    assert "do not form a matching pair" in vapid_configuration_error(_settings(other_public, private))


def test_vapid_validation_rejects_malformed_keys_and_subject():
    public, private = _key_pair()
    assert "public key" in vapid_configuration_error(_settings("bad", private))
    assert "private key" in vapid_configuration_error(_settings(public, "bad"))
    assert "subject" in vapid_configuration_error(_settings(public, private, "http://example.com"))
    assert "subject" in vapid_configuration_error(_settings(public, private, "https:///missing"))
    assert vapid_configuration_error(_settings(public, private, "https://solenosai.net/vapid")) is None


def test_vapid_validation_accepts_pem_private_key():
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    pem = private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii")
    assert vapid_configuration_error(_settings(_encode(public), pem.replace("\n", "\\n"))) is None
