
import base64
import hashlib
import json
import re

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, x25519


def require(value):
    if not value:
        raise ValueError("전달 입력 또는 대상 조건이 다릅니다.")


def validate_payload(payload):
    require(isinstance(payload, dict) and set(payload) == {
        "purpose", "gateway", "wireguard_private_key", "wireguard_public_key"
    })
    require(payload["purpose"] == "aws-gateway-wireguard")
    require(payload["gateway"] == "aws-vpn-gateway")

    decoded = []
    for field in ("wireguard_private_key", "wireguard_public_key"):
        value = payload[field]
        require(isinstance(value, str))
        raw = base64.b64decode(value, validate=True)
        require(len(raw) == 32 and any(raw))
        require(base64.b64encode(raw).decode("ascii") == value)
        decoded.append(raw)

    public = x25519.X25519PrivateKey.from_private_bytes(decoded[0]).public_key()
    require(public.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw
    ) == decoded[1])


def context_label(envelope):
    # 실제 수신 측은 승인된 세션 기록으로 기대값을 구성해야 한다.
    context = {name: envelope[name] for name in (
        "instance_id", "nonce", "expires",
        "archive_sha256", "wireguard_public_key"
    )}
    encoded = json.dumps(
        context, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).digest()


def seal(payload, challenge, expected_instance, archive_sha256, now):
    require(isinstance(challenge, dict) and set(challenge) == {
        "instance_id", "nonce", "expires", "public_pem"
    })
    require(
        isinstance(expected_instance, str)
        and re.fullmatch(
            r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})", expected_instance
        )
    )
    require(challenge["instance_id"] == expected_instance)
    require(
        isinstance(challenge["nonce"], str)
        and re.fullmatch(r"[0-9a-f]{64}", challenge["nonce"])
    )
    require(type(now) is int and type(challenge["expires"]) is int)
    require(now < challenge["expires"] <= now + 600)
    require(
        isinstance(archive_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", archive_sha256)
    )

    validate_payload(payload)

    pem = challenge["public_pem"]
    require(isinstance(pem, str) and len(pem) <= 4096)
    public = serialization.load_pem_public_key(pem.encode("ascii"))
    require(isinstance(public, rsa.RSAPublicKey) and public.key_size == 3072)
    require(public.public_numbers().e == 65537)

    envelope = {
        "instance_id": expected_instance,
        "nonce": challenge["nonce"],
        "expires": challenge["expires"],
        "archive_sha256": archive_sha256,
        "wireguard_public_key": payload["wireguard_public_key"],
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("ascii")
    require(len(raw) <= 318)

    encrypted = public.encrypt(
        raw,
        padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=context_label(envelope),
        ),
    )
    envelope["ciphertext"] = base64.b64encode(encrypted).decode("ascii")
    return envelope


if __name__ == "__main__":
    print("준비 후보: 실제 키·AWS·SSM·서버 작업은 실행하지 않습니다.")
