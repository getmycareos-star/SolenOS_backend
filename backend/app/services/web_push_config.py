import base64
import hmac
from urllib.parse import urlparse

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _decode_base64url(value: str) -> bytes | None:
    try:
        padded = value + ("=" * (-len(value) % 4))
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        return None


def vapid_configuration_error(settings) -> str | None:
    """Validate VAPID subject syntax and prove the configured P-256 key pair matches.

    VAPID keys are base64url-encoded: a 65-byte uncompressed P-256 public point
    and a 32-byte private scalar. No key material is included in returned errors.
    """
    public_text = (settings.vapid_public_key or "").strip()
    private_text = (settings.vapid_private_key or "").strip()
    subject = (settings.vapid_subject or "").strip()

    if not public_text or not private_text:
        return "VAPID public and private keys are both required"

    parsed = urlparse(subject)
    valid_mailto = parsed.scheme == "mailto" and bool(parsed.path) and "@" in parsed.path
    valid_https = (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )
    if not (valid_mailto or valid_https):
        return "VAPID subject must be a valid mailto: or HTTPS URI"

    public_bytes = _decode_base64url(public_text)
    private_bytes = _decode_base64url(private_text)
    if public_bytes is None or len(public_bytes) != 65 or public_bytes[0] != 0x04:
        return "VAPID public key must be a base64url P-256 uncompressed point"
    if private_bytes is None or len(private_bytes) != 32:
        return "VAPID private key must be a base64url 32-byte P-256 scalar"

    try:
        private_value = int.from_bytes(private_bytes, "big")
        private_key = ec.derive_private_key(private_value, ec.SECP256R1())
        expected_public = private_key.public_key().public_bytes(
            Encoding.X962,
            PublicFormat.UncompressedPoint,
        )
    except (ValueError, TypeError):
        return "VAPID private key is not a valid P-256 scalar"

    if not hmac.compare_digest(expected_public, public_bytes):
        return "VAPID public and private keys do not form a matching pair"

    return None
