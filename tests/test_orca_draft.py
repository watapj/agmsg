"""Draft bytes survive native SQLite output framing; no real terminal is used."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash")


class OrcaDraftTests(unittest.TestCase):
    def read_draft(self, draft, fail_sql=False):
        with tempfile.TemporaryDirectory(prefix="agmsg-draft-") as directory:
            response = Path(directory) / "response.json"
            response.write_text(json.dumps({"ok": True, "result": {"terminal": {"draft": draft}}}), encoding="utf-8")
            env = dict(os.environ, TEST_ORCA_RESPONSE=str(response))
            code = r'''
source scripts/drivers/terminals/orca/ops.sh
orca() {
  case "$2" in
    show) printf '%s\n' '{"ok":true,"result":{"terminal":{"agentIdentity":"fixture"}}}' ;;
    read) cat "$TEST_ORCA_RESPONSE" ;;
  esac
}
'''
            if fail_sql:
                code += r'''
sqlite3() {
  case "$*" in *'SELECT hex'*) return 77 ;; *) command sqlite3 "$@" ;; esac
}
'''
            code += "terminal_input_draft term_11111111-1111-4111-8111-111111111111\n"
            return subprocess.run([BASH, "-c", code], cwd=ROOT, env=env,
                                  capture_output=True, timeout=25)

    def test_empty_draft_has_no_phantom_carriage_return(self):
        result = self.read_draft("")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(base64.b64decode(result.stdout.strip()), b"")

    def test_draft_bytes_include_unicode_quotes_backslashes_and_trailing_newlines(self):
        for text in ["draft\n\n", "日本語 'quoted' \\path\r\n\r\n", "\r"]:
            with self.subTest(text=text):
                result = self.read_draft(text)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(base64.b64decode(result.stdout.strip()), text.encode("utf-8"))

    def test_sql_failure_is_not_confirmed_empty(self):
        result = self.read_draft("", fail_sql=True)
        self.assertEqual(result.returncode, 12, result.stderr)
        self.assertIn(b"sqlite3 exited 77", result.stderr)

    def test_non_text_draft_is_not_confirmed_empty(self):
        result = self.read_draft(None)
        self.assertEqual(result.returncode, 12, result.stderr)


if __name__ == "__main__":
    unittest.main()
