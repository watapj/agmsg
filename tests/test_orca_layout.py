"""Orca tab identity across split groups; no real terminal is contacted."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash")
HANDLE = "term_11111111-1111-4111-8111-111111111111"


class OrcaLayoutTests(unittest.TestCase):
    def response(self, root):
        return {"ok": True, "result": {
            "terminals": [{"handle": HANDLE, "tabId": "tab", "worktreeId": "wt"}],
            "visualLayouts": [{"worktreeId": "other", "root": {"tabs": [{"tabId": "tab", "title": "wrong"}]}},
                              {"worktreeId": "wt", "root": root}]}}

    def observe(self, response):
        with tempfile.TemporaryDirectory(prefix="agmsg-layout-") as directory:
            file = Path(directory) / "layout.json"
            file.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
            return subprocess.run([BASH, "-c", r'''
source scripts/drivers/terminals/orca/ops.sh
orca() { cat "$TEST_ORCA_JSON"; }
terminal_label_of "$TEST_ORCA_HANDLE"
'''], cwd=ROOT, env=dict(os.environ, TEST_ORCA_JSON=str(file), TEST_ORCA_HANDLE=HANDLE),
                capture_output=True, timeout=30)

    def test_split_tree_finds_exact_tab_and_worktree(self):
        group = {"type": "group", "tabs": [{"tabId": "tab", "title": "team:日本語"}]}
        result = self.observe(self.response({"type": "split", "first": {"type": "split", "second": group}}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.decode("utf-8").strip(), "team:日本語")

    def test_flat_layout_keeps_working(self):
        result = self.observe(self.response({"tabs": [{"tabId": "tab", "title": "team:alice"}]}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), b"team:alice")

    def test_duplicate_tab_is_not_chosen(self):
        group = {"tabs": [{"tabId": "tab", "title": "team:alice"}]}
        result = self.observe(self.response({"first": group, "second": copy.deepcopy(group)}))
        self.assertEqual(result.returncode, 10, result.stderr)
        self.assertEqual(result.stdout, b"")

    def test_title_controls_are_not_stripped_into_a_false_match(self):
        for title in ["team:alice\n", "team:alice\t", "team:alice\r", "team:alice\x00"]:
            with self.subTest(title=title):
                result = self.observe(self.response({"tabs": [{"tabId": "tab", "title": title}]}))
                self.assertEqual(result.returncode, 10, result.stderr)
                self.assertEqual(result.stdout, b"")

    def test_large_layout_does_not_cross_native_argument_limit(self):
        response = self.response({"tabs": [{"tabId": "tab", "title": "team:alice"}]})
        response["result"]["padding"] = "日本語" * 40000
        result = self.observe(response)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), b"team:alice")


if __name__ == "__main__":
    unittest.main()
