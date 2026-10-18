"""Test the production renewal decision without a startup-dependent deadline."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash")
SOURCE = (ROOT / "scripts/watch.sh").read_text(encoding="utf-8")
FUNCTION = SOURCE.split("_watch_renew_or_stop() {", 1)[1].split("\n}", 1)[0]


class WatchRenewalTests(unittest.TestCase):
    def decision(self, delivered=0, keep=None, elapsed=1750):
        env = dict(os.environ)
        env.pop("AGMSG_CC_MONITOR_KEEP_ALIVE", None)
        if keep is not None:
            env["AGMSG_CC_MONITOR_KEEP_ALIVE"] = keep
        code = f'''
SCRIPT_DIR="/tmp/skill o'brien/scripts"
ACTIVE_NAME=alice
ORIG_ARGS=(session "/tmp/project o'brien" claude-code alice --max-seconds=1750)
WATCH_MAX_SECONDS=1750
_WATCH_ELAPSED_BASE={elapsed}
_WATCH_DELIVERED={delivered}
watch_report() {{ printf '%s\\n' "$*"; }}
_watch_renew_or_stop() {{{FUNCTION}
}}
_watch_renew_or_stop
printf 'continues\\n'
'''
        result = subprocess.run([BASH, "-c", code], cwd=ROOT, env=env,
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_idle_renews_by_default(self):
        output = self.decision()
        self.assertIn("re-arm -", output)
        self.assertIn("(acting as alice)", output)
        self.assertIn("timeout_ms: 1800000", output)

    def test_idle_stops_only_with_explicit_opt_out(self):
        self.assertIn("stopping -", self.decision(keep="off"))

    def test_delivery_renews_even_with_idle_opt_out(self):
        self.assertIn("re-arm -", self.decision(delivered=1, keep="off"))

    def test_before_cap_continues_without_a_renewal_event(self):
        self.assertEqual(self.decision(elapsed=0), "continues\n")


if __name__ == "__main__":
    unittest.main()
