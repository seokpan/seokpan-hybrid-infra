# 작성자: 이유빈
# 작성일: 2026-10-20
# 작성 내용: 새 서비스 등록·중단·미실행 흐름만 검사

import ast, re, subprocess, textwrap, time, types, unittest
from pathlib import Path
from unittest.mock import Mock
import secrets

ROOT = Path(__file__).resolve().parent

def load_launcher():
    text = (ROOT / "vpn_aws_session_open.yml").read_text(encoding="utf-8")
    pattern = (
        r'(?ms)^          - \|\n(.*?)'
        r'(?=^          - "\{\{ vpn_runtime_destination \}\}")'
    )
    match = re.search(pattern, text)
    tree = ast.parse(textwrap.dedent(match.group(1)))
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "launch_receiver"
    ]
    worker = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "WORKER"
            for target in node.targets
        )
    )
    namespace = dict(
        subprocess=subprocess,
        time=time,
        secrets=secrets,
        WORKER=worker,
    )
    exec(
        compile(
            ast.Module(body=nodes, type_ignores=[]),
            "<review-launcher>",
            "exec",
        ),
        namespace,
    )
    return namespace["launch_receiver"]

class FakeServer:
    def __init__(self, fail=False):
        self.fail = fail
        self.key = False
        self.armed = False
        self.events = []

    def run(self, command, **kwargs):
        if command[0] == "systemd-run":
            assert "--property=Type=exec" in command
            assert "--property=RuntimeMaxSec=360" in command
            assert "--property=TimeoutStopSec=5s" in command
            assert any(
                value.startswith("--property=ExecStopPost=")
                and "vpn_aws_receiver_cleanup.py --apply" in value
                for value in command
            )
            if self.fail:
                return types.SimpleNamespace(returncode=1)
            self.armed = True
            self.events.append("정리 등록")
            self.key = True
            self.events.append("가짜 키 생성")
        elif command[:2] == ["systemctl", "stop"]:
            self.expire()
        return types.SimpleNamespace(returncode=0)

    def expire(self):
        if self.armed:
            self.key = False
            self.events.append("독립 정리")

class ReviewFixTests(unittest.TestCase):
    def call(self, server, reader):
        return load_launcher()(
            Path("/opt/seokpan-vpn-runtime-" + "a" * 16),
            Path("/run/seokpan-vpn-receiver-test"),
            reader,
            runner=server.run,
        )

    def test_three_plugin_paths_match_existing_inventory(self):
        expected = "/usr/local/sessionmanagerplugin/bin/session-manager-plugin"
        for name in (
            "vpn_aws_runtime_deploy.yml",
            "vpn_aws_session_open.yml",
            "vpn_aws_key_supply.yml",
        ):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn(
                'ansible_aws_ssm_plugin: "' + expected + '"',
                text,
            )
            self.assertNotIn(
                "/usr/local/bin/session-manager-plugin",
                text,
            )

    def test_service_registration_failure_creates_no_key(self):
        server = FakeServer(fail=True)
        reader = Mock()
        with self.assertRaises(ValueError):
            self.call(server, reader)
        reader.assert_not_called()
        self.assertFalse(server.key)

    def test_interrupt_after_creation_stops_and_cleans(self):
        server = FakeServer()
        with self.assertRaises(KeyboardInterrupt):
            self.call(server, Mock(side_effect=KeyboardInterrupt))
        self.assertEqual(
            server.events[:2],
            ["정리 등록", "가짜 키 생성"],
        )
        self.assertFalse(server.key)

    def test_next_play_not_started_still_has_server_cleanup(self):
        server = FakeServer()
        self.assertEqual(
            self.call(server, lambda _: {"ready": True}),
            {"ready": True},
        )
        self.assertTrue(server.key)
        server.expire()
        self.assertFalse(server.key)

    def test_cleanup_is_deployed_before_session(self):
        text = (ROOT / "vpn_aws_runtime_deploy.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "      - vpn_aws_receiver_cleanup.py\n", text
        )
        supply = (ROOT / "vpn_aws_key_supply.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("--on-active=360", supply)

    def test_cleanup_scope_preserves_operation_key_and_records(self):
        text = (ROOT / "vpn_aws_receiver_cleanup.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "target = directory / 'receiver-rsa-private.pem'",
            text,
        )
        self.assertNotIn("rmtree", text)
        self.assertNotIn("wg0.key", text)
        self.assertNotIn("install-started", text)

if __name__ == "__main__":
    print(
        "새 검사 6개만 실행합니다. "
        "가짜 키 상태·대체 systemd 응답 사용.",
        flush=True,
    )
    unittest.main(verbosity=2)
