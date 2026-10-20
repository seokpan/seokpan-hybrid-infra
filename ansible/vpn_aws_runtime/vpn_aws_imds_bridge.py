"""Receiver identity bridge candidate.

This module is not production-ready.
Real authorization and SSM transport require separate review.
"""

import json
import re
import urllib.request


IMDS = "http://169.254.169.254"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_imds_identity(opener=None):
    """Read local EC2 identity using IMDSv2, without proxies."""

    if opener is None:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({})
        )

    token_req = urllib.request.Request(
        IMDS + "/latest/api/token",
        data=b"",
        method="PUT",
        headers={
            "X-aws-ec2-metadata-token-ttl-seconds": "60"
        },
    )

    with opener.open(token_req, timeout=3) as response:
        token = response.read(1025).decode("ascii")

    require(
        1 <= len(token) <= 1024
        and "\n" not in token
        and "\r" not in token,
        "invalid metadata token",
    )

    doc_req = urllib.request.Request(
        IMDS + "/latest/dynamic/instance-identity/document",
        headers={"X-aws-ec2-metadata-token": token},
    )

    with opener.open(doc_req, timeout=3) as response:
        document = response.read(8193)

    require(
        len(document) <= 8192,
        "identity document too large",
    )

    return json.loads(document)


def verify_identity(approved, observed):
    """Compare locally observed identity with approved values."""

    fields = {
        "account_id": "accountId",
        "region": "region",
        "instance_id": "instanceId",
        "image_id": "imageId",
        "private_ip": "privateIp",
    }

    require(
        isinstance(approved, dict)
        and isinstance(observed, dict),
        "identity structure mismatch",
    )

    require(
        approved.get("region") == "ap-northeast-2",
        "unapproved region",
    )

    require(
        isinstance(approved.get("account_id"), str)
        and re.fullmatch(
            r"[0-9]{12}", approved["account_id"]
        ),
        "invalid account approval",
    )

    require(
        isinstance(approved.get("instance_id"), str)
        and re.fullmatch(
            r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})",
            approved["instance_id"],
        ),
        "invalid instance approval",
    )

    for expected, actual in fields.items():
        value = approved.get(expected)

        require(
            isinstance(value, str)
            and observed.get(actual) == value,
            "identity mismatch: " + expected,
        )

    return approved["instance_id"]


def prepare_receiver_session(
    parent,
    approved,
    archive_sha256,
    wireguard_public_key,
    now,
    prepare_function,
    identity_reader,
):
    """Bind observed identity to existing session preparation."""

    # The reader must obtain identity independently.
    # Never use an instance ID copied from the envelope.
    observed = identity_reader()

    observed_instance = verify_identity(
        approved, observed
    )

    return prepare_function(
        parent=parent,
        approved_instance=approved["instance_id"],
        observed_instance=observed_instance,
        archive_sha256=archive_sha256,
        wireguard_public_key=wireguard_public_key,
        now=now,
        lifetime=300,
    )
