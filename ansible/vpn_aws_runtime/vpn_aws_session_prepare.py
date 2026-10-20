"""Offline candidate only. Trusted AWS identity is NOT implemented."""

import base64
import binascii
import json
import os
import re
import stat
from pathlib import Path
from secrets import token_hex

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def require(condition, message):
    if not condition:
        raise ValueError(message)


def secure_parent(parent):
    path = Path(parent)
    require(path.is_absolute(), "absolute path required")
    require(
        not any(p.is_symlink() for p in (path, *path.parents)),
        "symlink path rejected",
    )

    info = path.stat()
    require(stat.S_ISDIR(info.st_mode), "not a directory")
    require(info.st_uid == os.geteuid(), "wrong owner")
    require(stat.S_IMODE(info.st_mode) == 0o700, "mode must be 0700")
    return path


def write_new(directory, name, content):
    fd = os.open(
        directory / name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(fd, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def prepare_session(
    parent,
    approved_instance,
    observed_instance,
    archive_sha256,
    wireguard_public_key,
    now,
    lifetime=300,
):
    # observed_instance is a simulated input.
    # Real AWS identity verification must be added separately.
    require(
        isinstance(approved_instance, str)
        and re.fullmatch(
            r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})",
            approved_instance,
        ),
        "invalid approved instance",
    )
    require(
        observed_instance == approved_instance,
        "instance mismatch",
    )
    require(
        isinstance(archive_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", archive_sha256),
        "invalid archive hash",
    )
    require(
        isinstance(wireguard_public_key, str),
        "invalid public key type",
    )

    try:
        raw = base64.b64decode(
            wireguard_public_key, validate=True
        )
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid public key encoding") from exc

    require(
        len(raw) == 32
        and any(raw)
        and base64.b64encode(raw).decode("ascii")
        == wireguard_public_key,
        "invalid public key",
    )
    require(
        type(now) is int
        and type(lifetime) is int
        and 1 <= lifetime <= 600,
        "invalid lifetime",
    )

    base = secure_parent(parent)
    directory = base / "session"

    # Existing sessions must never be reused.
    os.mkdir(directory, 0o700)

    private = rsa.generate_private_key(
        public_exponent=65537,
        key_size=3072,
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")

    challenge = {
        "instance_id": approved_instance,
        "nonce": token_hex(32),
        "expires": now + lifetime,
        "public_pem": public_pem,
    }

    session = {
        "instance_id": approved_instance,
        "nonce": challenge["nonce"],
        "expires": challenge["expires"],
        "archive_sha256": archive_sha256,
        "wireguard_public_key": wireguard_public_key,
        "used": False,
    }

    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )

    # Offline-only prototype; not ready for production.
    write_new(
        directory,
        "receiver-rsa-private.pem",
        private_pem,
    )
    write_new(
        directory,
        "session.json",
        json.dumps(session, separators=(",", ":")).encode("ascii"),
    )
    write_new(
        directory,
        "challenge.json",
        json.dumps(challenge, separators=(",", ":")).encode("ascii"),
    )

    fd = os.open(
        directory,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

    return directory
