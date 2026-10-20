import ast
import hashlib
import importlib.util
import io
import json
import os
import py_compile
import tempfile
import unittest
from pathlib import Path

SOURCE = (
    Path(__file__).resolve().parent
    / "test_vpn_aws_runtime_integration.py"
)


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

        tree = ast.parse(SOURCE.read_bytes())
        node = next(
            n for n in tree.body
            if isinstance(n, ast.FunctionDef)
            and n.name == "load_verified"
        )

        # 로딩 함수 하나만 가져온다.
        # 기존 통합시험은 실행하지 않는다.
        namespace = {
            "ROOT": self.root,
            "hashlib": hashlib,
            "json": json,
        }
        exec(
            compile(
                ast.Module(body=[node], type_ignores=[]),
                str(SOURCE),
                "exec",
            ),
            namespace,
        )
        self.load = namespace["load_verified"]
        self.path = self.root / "candidate.py"

    def manifest(self):
        data = {
            "files": {
                "candidate.py":
                    hashlib.sha256(
                        self.path.read_bytes()
                    ).hexdigest()
            }
        }
        (self.root / "candidate-manifest.json").write_text(
            json.dumps(data),
            encoding="utf-8",
        )

    def test_cached_module_is_ignored(self):
        self.path.write_bytes(b'VALUE = "cache"\n')
        info = self.path.stat()
        cache = Path(
            importlib.util.cache_from_source(str(self.path))
        )

        py_compile.compile(str(self.path), doraise=True)
        cached_bytes = cache.read_bytes()

        self.path.write_bytes(b'VALUE = "fresh"\n')
        os.utime(
            self.path,
            ns=(info.st_atime_ns, info.st_mtime_ns),
        )
        self.manifest()

        self.assertEqual(
            self.load(
                "candidate.py", "checked_candidate"
            ).VALUE,
            "fresh",
        )
        self.assertEqual(cache.read_bytes(), cached_bytes)

    def test_changed_source_is_rejected(self):
        self.path.write_bytes(b'VALUE = "fresh"\n')
        self.manifest()
        self.path.write_bytes(b'VALUE = "other"\n')

        with self.assertRaises(RuntimeError):
            self.load("candidate.py", "changed_candidate")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        LoaderTests
    )
    result = unittest.TextTestRunner(
        stream=io.StringIO()
    ).run(suite)

    print(json.dumps({
        "검사 수": result.testsRun,
        "실패 수": len(result.failures),
        "오류 수": len(result.errors),
        "건너뜀 수": len(result.skipped),
        "검사 성공": result.wasSuccessful(),
        "실패한 검사": [
            t.id().split(".")[-1]
            for t, _ in result.failures + result.errors
        ],
    }, ensure_ascii=False))

    raise SystemExit(0 if result.wasSuccessful() else 1)
