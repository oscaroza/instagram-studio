"""Generate a Render-ready VAPID key pair without printing secrets."""

import base64
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def urlsafe(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


private_key = ec.generate_private_key(ec.SECP256R1())
private_value = private_key.private_numbers().private_value.to_bytes(32, "big")
public_value = private_key.public_key().public_bytes(
    Encoding.X962,
    PublicFormat.UncompressedPoint,
)

target = Path(".vapid.env")
target.write_text(
    f"VAPID_PUBLIC_KEY={urlsafe(public_value)}\n"
    f"VAPID_PRIVATE_KEY={urlsafe(private_value)}\n",
    encoding="utf-8",
)
target.chmod(0o600)
print("Clés créées dans .vapid.env (fichier ignoré par Git).")
