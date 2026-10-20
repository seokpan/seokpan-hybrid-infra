import copy
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location(
    "vpn_target_gate", ROOT / "vpn_aws_target_gate.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakeSTS:
    def __init__(self, account):
        self.account = account

    def get_caller_identity(self):
        return {"Account": self.account}


class FakeEC2:
    def __init__(self, response):
        self.response = response
        self.meta = SimpleNamespace(region_name="ap-northeast-2")

    def describe_instances(self, **kwargs):
        return self.response


class FakeSSM:
    def __init__(self, response):
        self.response = response
        self.meta = SimpleNamespace(region_name="ap-northeast-2")

    def describe_instance_information(self, **kwargs):
        return self.response


class GateTests(unittest.TestCase):
    def setUp(self):
        self.approved = {
            "account_id": "000000000000",
            "region": "ap-northeast-2",
            "instance_id": "i-0123456789abcdef0",
            "vpc_id": "vpc-abcdef12",
            "subnet_id": "subnet-abcdef12",
            "network_interface_id": "eni-abcdef12",
            "security_group_id": "sg-abcdef12",
            "image_id": "ami-0123456789abcdef0",
            "private_ip": "192.168.64.10",
        }

        self.ec2_response = {
            "Reservations": [{
                "OwnerId": "000000000000",
                "Instances": [{
                    "InstanceId": "i-0123456789abcdef0",
                    "VpcId": "vpc-abcdef12",
                    "SubnetId": "subnet-abcdef12",
                    "ImageId": "ami-0123456789abcdef0",
                    "InstanceType": "t3.micro",
                    "PrivateIpAddress": "192.168.64.10",
                    "State": {"Name": "running"},
                    "Tags": [
                        {"Key": "Name", "Value": "seokpan-fnd-vpn"},
                        {"Key": "Project", "Value": "seokpan"},
                        {"Key": "Phase", "Value": "2"},
                        {"Key": "Component", "Value": "hybrid"},
                    ],
                    "IamInstanceProfile": {
                        "Arn": (
                            "arn:aws:iam::000000000000:"
                            "instance-profile/seokpan-fnd-vpn-ssm"
                        )
                    },
                    "NetworkInterfaces": [{
                        "NetworkInterfaceId": "eni-abcdef12",
                        "PrivateIpAddress": "192.168.64.10",
                        "Attachment": {"DeviceIndex": 0},
                        "SourceDestCheck": False,
                        "Groups": [{"GroupId": "sg-abcdef12"}],
                    }],
                }],
            }],
        }

        self.ssm_response = {
            "InstanceInformationList": [{
                "InstanceId": "i-0123456789abcdef0",
                "ResourceType": "EC2Instance",
                "PlatformType": "Linux",
                "PingStatus": "Online",
            }]
        }

    def run_gate(self, account=None):
        return module.check_target(
            self.approved,
            FakeSTS(account or self.approved["account_id"]),
            FakeEC2(self.ec2_response),
            FakeSSM(self.ssm_response),
        )

    def test_approved_target(self):
        result = self.run_gate()
        self.assertTrue(result["target_verified"])
        self.assertTrue(result["ssm_online"])

    def test_wrong_account(self):
        with self.assertRaises(ValueError):
            self.run_gate(account="111111111111")

    def test_wrong_ami(self):
        self.ec2_response["Reservations"][0][
            "Instances"
        ][0]["ImageId"] = "ami-fffffffffffffffff"
        with self.assertRaises(ValueError):
            self.run_gate()

    def test_wrong_subnet(self):
        self.ec2_response["Reservations"][0][
            "Instances"
        ][0]["SubnetId"] = "subnet-ffffffff"
        with self.assertRaises(ValueError):
            self.run_gate()

    def test_wrong_security_group(self):
        eni = self.ec2_response["Reservations"][0][
            "Instances"
        ][0]["NetworkInterfaces"][0]
        eni["Groups"][0]["GroupId"] = "sg-ffffffff"
        with self.assertRaises(ValueError):
            self.run_gate()

    def test_ssm_offline(self):
        self.ssm_response["InstanceInformationList"][
            0
        ]["PingStatus"] = "ConnectionLost"
        with self.assertRaises(ValueError):
            self.run_gate()

    def test_wrong_ssm_instance(self):
        self.ssm_response["InstanceInformationList"][
            0
        ]["InstanceId"] = "i-fffffffffffffffff"
        with self.assertRaises(ValueError):
            self.run_gate()

    def test_non_vpn_tag(self):
        self.ec2_response["Reservations"][0][
            "Instances"
        ][0]["Tags"][0]["Value"] = "other-instance"
        with self.assertRaises(ValueError):
            self.run_gate()


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        GateTests
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    if not result.wasSuccessful():
        print("RESULT=STOP")
        sys.exit(1)

    print("RESULT=AWS_TARGET_GATE_OFFLINE_PASS")
