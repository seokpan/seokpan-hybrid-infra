# 실제 키·복호화·SSH·AWS 없이 실행 저장소 선택만 검사한다.
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "vpn_source_under_test", Path(__file__).with_name("vpn_onprem_key_supply.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SourceGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("GIT_")}

    def git(self, repo, *args):
        result = subprocess.run(
            ["git", "-c", "core.autocrlf=false", *args],
            cwd=str(repo), env=self.env, capture_output=True, text=True,
            timeout=15, check=True,
        )
        return result.stdout.strip()

    def repository(self, name):
        repo = self.root / name
        (repo / "scripts").mkdir(parents=True)
        script = repo / "scripts/vpn_onprem_key_supply.py"
        script.write_text("# 시험용 파일\n", encoding="utf-8")
        self.git(repo, "init", "-q", "--initial-branch=main")
        self.git(repo, "add", "--", "scripts/vpn_onprem_key_supply.py")
        self.git(repo, "-c", "user.name=Source Gate Test",
                 "-c", "user.email=source-test@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "시험 자료")
        return repo, script, self.git(repo, "rev-parse", "HEAD")

    def test_clean_current_repository(self):
        repo, script, sha = self.repository("current")
        actual, _ = module.verify_source(script, sha)
        self.assertEqual(actual, repo.resolve())

    def test_dirty_current_repository_with_other_clean_repository(self):
        _, other, other_sha = self.repository("other")
        module.verify_source(other, other_sha)
        _, current, current_sha = self.repository("current")
        current.write_text("# 미승인 변경\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            module.verify_source(current, current_sha)

    def test_wrong_source(self):
        _, script, _ = self.repository("current")
        with self.assertRaises(ValueError):
            module.verify_source(script, "0" * 40)

    def test_wrong_branch(self):
        repo, script, sha = self.repository("current")
        self.git(repo, "switch", "-qc", "unreviewed")
        with self.assertRaises(ValueError):
            module.verify_source(script, sha)

    def test_nested_folder_is_not_repository_root(self):
        repo, _, sha = self.repository("current")
        nested = repo / "nested/scripts/vpn_onprem_key_supply.py"
        nested.parent.mkdir(parents=True)
        nested.write_text("# 잘못된 위치\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            module.verify_source(nested, sha)


if __name__ == "__main__":
    unittest.main(verbosity=2)
