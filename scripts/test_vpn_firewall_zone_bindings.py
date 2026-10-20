# 작성자: 이유빈
# 작성일: 2026-10-21
# 작성 내용: NetworkManager 구역 연결 비교의 회귀 검사

import unittest
from pathlib import Path
import yaml

root = Path(__file__).resolve().parent.parent
play = yaml.safe_load(
    (root / "ansible/playbooks/vpn_onprem_configure.yml").read_text()
)[0]
task = next(
    item for item in play["pre_tasks"]
    if item["name"] == "NetworkManager 관리 연결을 포함한 구역 보존 검사"
)
source = task["ansible.builtin.command"]["argv"][-1]
scope = {"__name__": "zone_guard_test"}
exec(compile(source, "<방화벽 구역 비교>", "exec"), scope)
matches = scope["matches"]

saved = (
    "public\n"
    "  target: default\n"
    "  interfaces:\n"
    "  services: ssh\n"
    "trusted\n"
    "  target: ACCEPT\n"
    "  interfaces:\n"
)
current = saved.replace("public\n", "public (active)\n", 1).replace(
    "  interfaces:", "  interfaces: ens160", 1
)

class ZoneBindingTests(unittest.TestCase):
    def test_identical_settings(self):
        self.assertTrue(matches(saved, saved, "", "", ""))

    def test_networkmanager_default_public(self):
        self.assertTrue(matches(current, saved, "", "public", "public"))

    def test_networkmanager_explicit_public(self):
        self.assertTrue(matches(current, saved, "public", "drop", "public"))

    def test_wrong_profile_or_runtime_zone(self):
        self.assertFalse(matches(current, saved, "drop", "public", "public"))
        self.assertFalse(matches(current, saved, "", "public", "trusted"))

    def test_other_firewall_change_is_rejected(self):
        changed = current.replace("services: ssh", "services: ssh cockpit")
        self.assertFalse(matches(changed, saved, "", "public", "public"))

    def test_additional_interface_is_rejected(self):
        changed = current.replace("interfaces: ens160", "interfaces: ens160 ens192")
        self.assertFalse(matches(changed, saved, "", "public", "public"))

if __name__ == "__main__":
    unittest.main(verbosity=2)
