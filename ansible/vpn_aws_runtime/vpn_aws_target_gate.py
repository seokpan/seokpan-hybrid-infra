"""AWS VPN target verification candidate.

No AWS calls occur unless check_target receives real clients.
Not an SSM transport or session-authorization implementation.
"""

import re
from ipaddress import IPv4Address


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_target(approved, sts, ec2, ssm):
    fields = {
        "account_id", "region", "instance_id", "vpc_id",
        "subnet_id", "network_interface_id",
        "security_group_id", "image_id", "private_ip",
    }

    require(
        isinstance(approved, dict) and set(approved) == fields,
        "invalid approval contract",
    )
    require(
        re.fullmatch(r"[0-9]{12}", approved["account_id"]),
        "invalid account",
    )
    require(
        approved["region"] == "ap-northeast-2",
        "wrong region",
    )

    patterns = {
        "instance_id": r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})",
        "vpc_id": r"vpc-[0-9a-f]+",
        "subnet_id": r"subnet-[0-9a-f]+",
        "network_interface_id": r"eni-[0-9a-f]+",
        "security_group_id": r"sg-[0-9a-f]+",
        "image_id": r"ami-(?:[0-9a-f]{8}|[0-9a-f]{17})",
    }

    for key, pattern in patterns.items():
        require(
            isinstance(approved[key], str)
            and re.fullmatch(pattern, approved[key]),
            "invalid approved " + key,
        )

    require(
        isinstance(approved["private_ip"], str),
        "invalid approved IP",
    )
    IPv4Address(approved["private_ip"])

    for client in (ec2, ssm):
        require(
            getattr(getattr(client, "meta", None),
                    "region_name", None) == approved["region"],
            "client region mismatch",
        )

    identity = sts.get_caller_identity()
    require(
        identity.get("Account") == approved["account_id"],
        "account mismatch",
    )

    response = ec2.describe_instances(
        InstanceIds=[approved["instance_id"]]
    )

    reservations = response.get("Reservations", [])
    require(len(reservations) == 1, "EC2 reservation mismatch")

    reservation = reservations[0]
    require(
        reservation.get("OwnerId") == approved["account_id"],
        "EC2 owner mismatch",
    )

    instances = reservation.get("Instances", [])
    require(len(instances) == 1, "EC2 count mismatch")
    instance = instances[0]

    expected = {
        "InstanceId": approved["instance_id"],
        "VpcId": approved["vpc_id"],
        "SubnetId": approved["subnet_id"],
        "ImageId": approved["image_id"],
        "InstanceType": "t3.micro",
        "PrivateIpAddress": approved["private_ip"],
    }

    for key, value in expected.items():
        require(
            instance.get(key) == value,
            "EC2 " + key + " mismatch",
        )

    require(
        instance.get("State", {}).get("Name") == "running",
        "EC2 not running",
    )

    tags = {
        item["Key"]: item["Value"]
        for item in instance.get("Tags", [])
    }

    for key, value in {
        "Name": "seokpan-fnd-vpn",
        "Project": "seokpan",
        "Phase": "2",
        "Component": "hybrid",
    }.items():
        require(tags.get(key) == value, "EC2 tag mismatch")

    profile = (
        "arn:aws:iam::"
        + approved["account_id"]
        + ":instance-profile/seokpan-fnd-vpn-ssm"
    )
    require(
        instance.get("IamInstanceProfile", {}).get("Arn")
        == profile,
        "instance profile mismatch",
    )

    interfaces = instance.get("NetworkInterfaces", [])
    require(len(interfaces) == 1, "unexpected ENI count")
    eni = interfaces[0]

    require(
        eni.get("NetworkInterfaceId")
        == approved["network_interface_id"],
        "ENI mismatch",
    )
    require(
        eni.get("PrivateIpAddress") == approved["private_ip"],
        "ENI private IP mismatch",
    )
    require(
        eni.get("Attachment", {}).get("DeviceIndex") == 0,
        "primary ENI mismatch",
    )
    require(
        eni.get("SourceDestCheck") is False,
        "source/destination check enabled",
    )

    groups = eni.get("Groups", [])
    require(
        len(groups) == 1
        and groups[0].get("GroupId")
        == approved["security_group_id"],
        "security group mismatch",
    )

    managed = ssm.describe_instance_information(
        Filters=[
            {
                "Key": "InstanceIds",
                "Values": [approved["instance_id"]],
            }
        ],
        MaxResults=5,
    ).get("InstanceInformationList", [])

    require(len(managed) == 1, "SSM target mismatch")
    node = managed[0]

    require(
        node.get("InstanceId") == approved["instance_id"]
        and node.get("ResourceType") == "EC2Instance"
        and node.get("PlatformType") == "Linux"
        and node.get("PingStatus") == "Online",
        "SSM node not ready",
    )

    return {"target_verified": True, "ssm_online": True}
