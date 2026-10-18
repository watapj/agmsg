"""Windows reads do not wait for opportunistic pane discovery or rename."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = r"C:\Program Files\Git\bin\bash.exe"


@unittest.skipUnless(os.name == "nt", "Windows transport default")
class WindowsTransportTests(unittest.TestCase):
    def run_action(self, script, opt_in=False):
        with tempfile.TemporaryDirectory(prefix="agmsg-action-") as directory:
            root = Path(directory)
            scripts = root / "scripts"
            (scripts / "lib").mkdir(parents=True)
            shutil.copyfile(ROOT / "scripts" / script, scripts / script)
            (scripts / "lib/storage.sh").write_text(
                'agmsg_storage_load() { :; }\nstorage_store_exists() { return 1; }\n'
                'agmsg_db_path() { printf "%s" "$TEST_DB"; }\nstorage_send() { echo fixture-id; }\n', encoding="utf-8")
            (scripts / "lib/type-registry.sh").write_text("", encoding="utf-8")
            (scripts / "lib/validate.sh").write_text("agmsg_validate_team_name() { :; }\n", encoding="utf-8")
            (scripts / "lib/poke-mode.sh").write_text("agmsg_poke_target() { return 1; }\n", encoding="utf-8")
            (root / "fixture.db").touch()
            (scripts / "lib/self-name.sh").write_text(
                'agmsg_self_name_on_action() { : > "$TEST_NAMED"; }\n', encoding="utf-8")
            (scripts / "lib/self-rename.sh").write_text(
                'agmsg_self_rename_on_action() { : > "$TEST_RENAMED"; }\n', encoding="utf-8")
            env = dict(os.environ, TEST_NAMED=str(root / "named"), TEST_RENAMED=str(root / "renamed"),
                       TEST_DB=str(root / "fixture.db"))
            env.pop("AGMSG_SELF_NAME", None)
            if opt_in:
                env["AGMSG_SELF_NAME"] = "on"
            args = ["team", "alice", "bob", "hello", "--force"] if script == "send.sh" else ["team", "alice"]
            result = subprocess.run([BASH, str(scripts / script), *args], env=env,
                                    capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "named").exists(), opt_in)
            self.assertEqual((root / "renamed").exists(), opt_in)
            self.assertIn("Sent to" if script == "send.sh" else "No ", result.stdout)

    def test_inbox_does_not_discover_or_rename_before_reading(self):
        self.run_action("inbox.sh")

    def test_history_does_not_discover_or_rename_before_reading(self):
        self.run_action("history.sh")

    def test_send_does_not_discover_or_rename_before_transport(self):
        self.run_action("send.sh")

    def test_explicit_opt_in_preserves_legacy_action_naming(self):
        self.run_action("inbox.sh", opt_in=True)


if __name__ == "__main__":
    unittest.main()
