import base64
import hmac
from urllib.parse import urlparse

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _decode_base64url(value: str) -> bytes | None:
    try:
        padded = value + ("=" * (-len(value) % 4))
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        return None


def vapid_configuration_error(settings) -> str | None:
    """Validate the VAPID subject and prove that the configured P-256 key pair matches."""
    public_text = (settings.vapid_public_key or "").strip()
    private_text = (settings.vapid_private_key or "").strip()
    subject = (settings.vapid_subject or "").strip()

    if not public_text or not private_text:
        return "VAPID public and private keys are both required"

    try:
        parsed = urlparse(subject)
        hostname = parsed.hostname if parsed.scheme == "https" else None
    except ValueError:
        return "VAPID subject must be a valid mailto: or HTTPS URI"

    valid_mailto = (
        parsed.scheme == "mailto"
        and not parsed.netloc
        and bool(parsed.path)
        and "@" in parsed.path
        and not any(character.isspace() for character in parsed.path)
        and not parsed.fragment
    )
    valid_https = (
        parsed.scheme == "https"
        and bool(hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )
    if not (valid_mailto or valid_https):
        return "VAPID subject must be a valid mailto: or HTTPS URI"

    public_bytes = _decode_base64url(public_text)
    if public_bytes is None or len(public_bytes) != 65 or public_bytes[0] != 0x04:
        return "VAPID public key must be a base64url P-256 uncompressed point"

    try:
        if private_text.lstrip().startswith("-----BEGIN"):
            normalized_pem = private_text.replace("\\n", "\n").encode("ascii")
            private_key = serialization.load_pem_private_key(normalized_pem, password=None)
            if (
                not isinstance(private_key, ec.EllipticCurvePrivateKey)
                or not isinstance(private_key.curve, ec.SECP256R1)
            ):
                return "VAPID private key must be a PEM-encoded P-256 key"
        else:
            private_bytes = _decode_base64url(private_text)
            if private_bytes is None or len(private_bytes) != 32:
                return "VAPID private key must be a PEM P-256 key or base64url 32-byte scalar"
            private_key = ec.derive_private_key(int.from_bytes(private_bytes, "big"), ec.SECP256R1())

        expected_public = private_key.public_key().public_bytes(
            Encoding.X962,
            PublicFormat.UncompressedPoint,
        )
    except (ValueError, TypeError, UnicodeEncodeError):
        return "VAPID private key is not a valid P-256 key"

    if not hmac.compare_digest(expected_public, public_bytes):
        return "VAPID public and private keys do not form a matching pair"
    return None
