"""Native Windows transport checks, using only stdlib and installed PowerShell/Git.

Run from a normal terminal: python tests/test_windows_hook_queue.py
Fixtures never change the installed skill, Codex config, profiles or OS settings.
"""
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
ENGINE = os.environ.get("AGMSG_TEST_POWERSHELL") or shutil.which("powershell.exe")
BASH = os.environ.get("GIT_BASH", r"C:\Program Files\Git\bin\bash.exe")


@unittest.skipUnless(os.name == "nt" and ENGINE, "requires native Windows PowerShell")
class HookQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agmsg-hook-test-")
        self.root = Path(self.temp.name)
        # Exercise quotes, shell substitutions, brackets, unicode and spaces in
        # BOTH the install path and project path, not just the JSON payload.
        self.skill = self.root / "skill o'brien $x & [日本語]"
        shutil.copytree(ROOT / "scripts", self.skill / "scripts")
        self.project = self.root / "project o'brien $(touch INJECTED) & [日本語]"
        self.project.mkdir()
        self.queue = self.skill / "run" / "hook-queue"
        self.windows = self.skill / "scripts" / "windows"
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("AGMSG_", "CODEX_", "CLAUDE_"))}
        self.env.update(AGMSG_SELF_NAME="off", AGMSG_RESOLVE_PROJECT="0", AGMSG_AGENT_PID="")
        self.children = []
        self.real_start = (self.skill / "scripts/session-start.sh").read_text(encoding="utf-8")
        self.real_end = (self.skill / "scripts/session-end.sh").read_text(encoding="utf-8")
        for event, script in [("SessionStart", "session-start.sh"), ("SessionEnd", "session-end.sh")]:
            (self.skill / "scripts" / script).write_text(
                'set -eu\ncat > "$AGMSG_HOOK_PROJECT/' + event + '.stdin"\n'
                'printf "%s\\n" "$1" "$2" "$CODEX_THREAD_ID" "$AGMSG_CODEX_SEAT_KEY" '
                '"$AGMSG_CODEX_BRIDGE_LAUNCHER" >> "$AGMSG_HOOK_PROJECT/calls"\n',
                encoding="utf-8", newline="\n")
        self.enable()

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                self.ps("hook-dispatcher.ps1", "-Action", "Stop")
                try:
                    child.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
            child.communicate()
        self.temp.cleanup()

    def enable(self):
        directory = self.project / ".codex"
        directory.mkdir(exist_ok=True)
        command = str(self.windows / "hook-enqueue.ps1").replace("\\", "/").replace("'", "''")
        (directory / "hooks.json").write_text(json.dumps({"hooks": {
            event: [{"hooks": [{"type": "command", "commandWindows": "& '" + command + "'"}]}]
            for event in ("SessionStart", "SessionEnd")}}), encoding="utf-8")

    def seat_record(self, project_hash=None, port=50505):
        if project_hash is None:
            result = subprocess.run([BASH, '--noprofile', '--norc', '-c', 'cygpath -u "$1"', '_', str(self.project)],
                                    capture_output=True, encoding='utf-8', env=self.env, check=True)
            project_hash = hashlib.sha1(result.stdout.strip().encode('utf-8')).hexdigest()
        self.queue.parent.mkdir(parents=True, exist_ok=True)
        (self.queue.parent / "codex-app-server.123.456.7.record").write_text(
            'v=1\ngeneration=1.2.3\nproject=' + project_hash +
            '\npid=12345\nport=' + str(port) + '\nwitnesssrc=\nwitness=\nversion=test\n', encoding='utf-8', newline='\n')

    def ps(self, script, *args, input_text="", env=None, check=True):
        result = subprocess.run([ENGINE, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(self.windows / script), *map(str, args)],
                                input=input_text, encoding="utf-8", capture_output=True,
                                env=env or self.env, timeout=35)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def enqueue(self, event="SessionStart", payload=None, **env):
        text = payload if payload is not None else '{"session_id":"thread-1","cwd":"ignored","text":"日本語"}\r\n'
        # PS 5.1's redirected console input defaults to the OEM codepage. The
        # commandWindows host controls its encoding; explicitly use UTF-8 here.
        command = "& '" + str(
            self.windows / "hook-enqueue.ps1").replace("'", "''") + "' -Event '" + event + "' -Type codex -Project '" + str(
                self.project).replace("'", "''") + "'"
        process = subprocess.run([ENGINE, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                  "-Command", command], input=text.encode("utf-8"), capture_output=True,
                                 env=dict(self.env, **env), timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        return text, sorted(self.queue.glob("*.req"))

    def dispatch(self, *args, check=True):
        return self.ps("hook-dispatcher.ps1", "-Once", "-Project", self.project,
                       "-Bash", BASH, *args, check=check)

    def log(self):
        return (self.skill / "run/hook-dispatcher.log").read_text(encoding="utf-8-sig")

    def test_round_trip_both_events_and_duplicate(self):
        self.seat_record()
        text, requests = self.enqueue(AGMSG_CODEX_SEAT_KEY="123.456.7")
        saved = requests[0].read_bytes()
        self.enqueue("SessionEnd", AGMSG_CODEX_SEAT_KEY="123.456.7")
        self.dispatch()
        for event in ("SessionStart", "SessionEnd"):
            self.assertEqual((self.project / (event + ".stdin")).read_bytes(), text.encode("utf-8"))
        calls = (self.project / "calls").read_text(encoding="utf-8").splitlines()
        self.assertEqual(calls[::5], ["codex", "codex"])
        self.assertEqual(calls[2::5], ["thread-1", "thread-1"])
        self.assertEqual(calls[3::5], ["123.456.7", "123.456.7"])
        self.assertEqual(calls[4::5], ["1", "1"])
        requests[0].write_bytes(saved)
        self.dispatch()
        self.assertEqual((self.project / "calls").read_text(encoding="utf-8").splitlines(), calls)
        self.assertIn("status=duplicate", self.log())
        self.assertFalse(list(self.root.rglob("INJECTED")))

    def test_invalid_requests_cannot_choose_code_environment_or_project(self):
        _, requests = self.enqueue()
        original = json.loads(requests[0].read_text(encoding="utf-8"))
        requests[0].unlink()
        for change in ({"event": "Stop"}, {"type": "claude-code"}, {"project": str(self.root)},
                       {"command": "touch INJECTED"}, {"env": {"BASH_ENV": "evil"}},
                       {"seat": "../../evil"}, {"thread": "other-thread"},
                       {"stdin": '{"session_id":"../../evil"}'}, {"stdin": "{}"}):
            request = dict(original, **change, id=uuid.uuid4().hex)
            (self.queue / (request["id"] + ".req")).write_text(json.dumps(request), encoding="utf-8")
        self.dispatch()
        self.assertEqual(len(list(self.queue.glob("*.failed"))), 9)
        self.assertFalse((self.project / "calls").exists())

    def test_off_rejects_pending_requests(self):
        self.enqueue()
        (self.project / ".codex/hooks.json").write_text('{"hooks":{}}', encoding="utf-8")
        self.dispatch()
        self.assertIn("Monitor hook is disabled", self.log())
        self.assertFalse((self.project / "calls").exists())

    def test_failed_and_interrupted_requests_are_retained_without_replay(self):
        (self.skill / "scripts/session-start.sh").write_text("exit 7\n", encoding="utf-8")
        _, requests = self.enqueue()
        self.dispatch()
        self.assertTrue(requests[0].with_suffix(".failed").exists())
        self.assertIn("Hook exited 7", self.log())
        _, requests = self.enqueue()
        requests[0].rename(requests[0].with_suffix(".processing"))
        self.dispatch()
        self.assertTrue(requests[0].with_suffix(".uncertain").exists())
        self.assertFalse((self.project / "calls").exists())

    def test_enqueue_failure_is_fail_open_and_visible(self):
        self.queue.parent.mkdir(parents=True, exist_ok=True)
        self.queue.write_text("blocked", encoding="utf-8")
        result = self.ps("hook-enqueue.ps1", "-Event", "SessionStart", "-Type", "codex",
                         "-Project", self.project, input_text='{"session_id":"thread-1"}')
        self.assertIn("agmsg hook enqueue failed:", result.stderr)
        self.assertEqual(self.queue.read_text(encoding="utf-8"), "blocked")

    def test_single_dispatcher_and_cooperative_stop(self):
        self.queue.mkdir(parents=True)
        child = subprocess.Popen([ENGINE, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                  "-File", str(self.windows / "hook-dispatcher.ps1"),
                                  "-Project", str(self.project), "-Bash", BASH],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
        self.children.append(child)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if (self.skill / "run/hook-dispatcher.log").exists():
                break
            if child.poll() is not None:
                self.fail(str(child.communicate()))
            time.sleep(0.05)
        self.assertIn("Dispatcher running", self.ps("hook-dispatcher.ps1", "-Action", "Status").stdout)
        self.ps("hook-dispatcher.ps1", "-Action", "Start", "-Project", str(self.project))
        other = self.root / "other-project"
        other.mkdir()
        result = self.ps("hook-dispatcher.ps1", "-Action", "Start", "-Project", str(other), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not allowed", result.stderr)
        self.assertNotEqual(self.dispatch(check=False).returncode, 0)
        self.enqueue()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not list(self.queue.glob("*.done")):
            time.sleep(0.05)
        self.assertEqual(len(list(self.queue.glob("*.done"))), 1)
        self.ps("hook-dispatcher.ps1", "-Action", "Stop")
        self.assertEqual(child.wait(timeout=10), 0)
        self.assertIn("Dispatcher stopped", self.ps("hook-dispatcher.ps1", "-Action", "Status").stdout)

    def test_real_session_start_publishes_existing_seat_request(self):
        self.seat_record()
        (self.skill / "scripts/session-start.sh").write_text(self.real_start, encoding="utf-8", newline="\n")
        def bash(code):
            result = subprocess.run([BASH, "--noprofile", "--norc", "-c", code, "_", str(self.skill), str(self.project)],
                                    capture_output=True, encoding="utf-8", env=self.env, timeout=40)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout
        bash('bash "$1/scripts/internal/init-db.sh"; bash "$1/scripts/join.sh" team alice codex "$(cd "$2" && pwd)"')
        bash('SKILL_DIR=$(cd "$1" && pwd); source "$SKILL_DIR/scripts/lib/role-session.sh"; '
             'agmsg_role_session_record team alice thread-1 "$(cd "$2" && pwd)" codex')
        recorded = bash('SKILL_DIR=$(cd "$1" && pwd); source "$SKILL_DIR/scripts/lib/role-session.sh"; '
                        'agmsg_role_session_uuid team alice; agmsg_role_session_get team alice project')
        self.assertIn('thread-1', recorded)
        self.enqueue(AGMSG_CODEX_SEAT_KEY="123.456.7")
        self.dispatch()
        request = self.skill / "run/codex-bridge-request.123.456.7"
        self.assertTrue(request.exists(), self.log())
        self.assertTrue(request.read_text(encoding="utf-8").startswith("codex\tthread-1\t"))
        self.assertIn("ws://127.0.0.1:50505\tteam\talice", request.read_text(encoding="utf-8"), self.log()[-12000:])

    def test_seat_cannot_cross_project_boundary(self):
        self.seat_record("0" * 40)
        self.enqueue(AGMSG_CODEX_SEAT_KEY="123.456.7")
        self.dispatch()
        self.assertIn("seat project mismatch", self.log())
        self.assertFalse((self.project / "calls").exists())

    def test_real_session_end_cleans_the_matching_session(self):
        (self.skill / 'scripts/session-end.sh').write_text(self.real_end, encoding='utf-8', newline='\n')
        self.enqueue('SessionEnd')
        marker = self.queue.parent / 'cc-instance.12345'
        marker.write_text('thread-1', encoding='utf-8')
        self.dispatch()
        self.assertEqual(len(list(self.queue.glob('*.done'))), 1, self.log())
        self.assertFalse(marker.exists())

    def test_generated_commands_enqueue_without_bash_in_the_hook(self):
        (self.project / '.codex/hooks.json').unlink()
        result = subprocess.run([BASH, "--noprofile", "--norc", "-c",
                                 'bash "$1/scripts/delivery.sh" set monitor codex "$2"',
                                 "_", str(self.skill), str(self.project)],
                                capture_output=True, encoding="utf-8", env=self.env, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        hooks = json.loads((self.project / ".codex/hooks.json").read_text(encoding="utf-8"))["hooks"]
        payload = '{"session_id":"thread-1","text":"日本語"}\r\n'.encode('utf-8')
        for event in ("SessionStart", "SessionEnd"):
            command = hooks[event][0]["hooks"][0]["commandWindows"]
            self.assertIn("hook-enqueue.ps1", command)
            self.assertNotIn("bash.exe", command)
            # Invalid Bash configuration proves hook enqueue never starts Bash.
            result = subprocess.run([ENGINE, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                     "-Command", command], input=payload,
                                    capture_output=True, env=dict(self.env, GIT_BASH="missing.exe"), timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(result.stderr, command + '\n' + repr(result.stderr))
        self.assertEqual(len(list(self.queue.glob("*.req"))), 2)
        self.dispatch()
        self.assertEqual(len(list(self.queue.glob("*.done"))), 2, self.log())
        self.assertEqual((self.project / 'SessionStart.stdin').read_bytes(), payload)

    def test_enqueue_needs_no_msys_process(self):
        text, requests = self.enqueue(GIT_BASH="missing.exe", AGMSG_BASH="missing.exe")
        self.assertEqual(len(requests), 1)
        request = json.loads(requests[0].read_text(encoding="utf-8"))
        self.assertEqual(request['stdin'], text)
        self.assertEqual(request['type'], 'codex')
        self.assertEqual(request['project'], str(self.project))

    def test_missing_bash_preserves_pending_work(self):
        self.enqueue()
        result = self.ps('hook-dispatcher.ps1', '-Once', '-Project', self.project,
                         '-Bash', self.root / 'missing-bash.exe', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(list(self.queue.glob('*.req'))), 1)
        self.assertIn('Dispatcher stopped', self.ps('hook-dispatcher.ps1', '-Action', 'Status').stdout)

    def test_background_start_stop_and_timeout_quarantine(self):
        self.ps('hook-dispatcher.ps1', '-Action', 'Start', '-Project', self.project, '-Bash', BASH)
        try:
            self.assertIn('Dispatcher running', self.ps('hook-dispatcher.ps1', '-Action', 'Status').stdout)
            self.enqueue()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not list(self.queue.glob('*.done')):
                time.sleep(0.05)
            self.assertEqual(len(list(self.queue.glob('*.done'))), 1, self.log())
        finally:
            self.ps('hook-dispatcher.ps1', '-Action', 'Stop')
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if 'Dispatcher stopped' in self.ps('hook-dispatcher.ps1', '-Action', 'Status').stdout:
                    break
                time.sleep(0.1)
            else:
                self.fail('Background dispatcher failed to stop')
        (self.skill / 'scripts/session-start.sh').write_text('cat >/dev/null\nsleep 30\n', encoding='utf-8')
        self.enqueue()
        self.dispatch('-HookTimeoutSeconds', 1)
        self.assertEqual(len(list(self.queue.glob('*.uncertain'))), 1, self.log())
        self.assertIn('Hook timed out', self.log())

    def test_junction_project_is_rejected(self):
        junction = self.root / 'junction'
        target = self.root / 'junction-target'
        target.mkdir()
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        result = subprocess.run([ENGINE, '-NoProfile', '-NonInteractive', '-Command',
                                 'New-Item -ItemType Junction -Path ' + quote(junction) + ' -Target ' + quote(target)],
                                capture_output=True, env=self.env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        _, requests = self.enqueue()
        request = json.loads(requests[0].read_text(encoding='utf-8'))
        request['project'] = str(junction)
        requests[0].write_text(json.dumps(request), encoding='utf-8')
        self.dispatch()
        self.assertIn('Reparse points are not supported', self.log())
        self.assertFalse((self.project / 'calls').exists())

    def test_queue_to_real_bridge_inlines_a_message_on_the_recorded_thread(self):
        import base64
        import socketserver
        import struct
        import threading

        turns = []
        class AppServer(socketserver.StreamRequestHandler):
            def send(self, message):
                data = json.dumps(message).encode('utf-8')
                length = bytes([len(data)]) if len(data) < 126 else b'\x7e' + struct.pack('!H', len(data))
                self.wfile.write(b'\x81' + length + data)
                self.wfile.flush()

            def handle(self):
                self.connection.settimeout(30)
                headers = {}
                self.rfile.readline()
                while True:
                    line = self.rfile.readline().strip()
                    if not line:
                        break
                    key, value = line.split(b':', 1)
                    headers[key.lower()] = value.strip()
                accept = base64.b64encode(hashlib.sha1(headers[b'sec-websocket-key'] +
                    b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
                self.wfile.write(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n'
                                 b'Connection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + b'\r\n\r\n')
                self.wfile.flush()
                while True:
                    header = self.rfile.read(2)
                    if len(header) != 2 or header[0] & 15 == 8:
                        return
                    length = header[1] & 127
                    if length == 126:
                        length = struct.unpack('!H', self.rfile.read(2))[0]
                    elif length == 127:
                        length = struct.unpack('!Q', self.rfile.read(8))[0]
                    mask = self.rfile.read(4) if header[1] & 128 else None
                    data = self.rfile.read(length)
                    if mask:
                        data = bytes(value ^ mask[i % 4] for i, value in enumerate(data))
                    message = json.loads(data)
                    if 'id' not in message:
                        continue
                    method = message['method']
                    result = {'thread': {'id': 'thread-1', 'status': {'type': 'idle'}}} if method == 'thread/resume' else {}
                    self.send({'jsonrpc': '2.0', 'id': message['id'], 'result': result})
                    if method == 'process/spawn':
                        time.sleep(0.05)
                        self.send({'jsonrpc': '2.0', 'method': 'process/exited', 'params': {
                            'processHandle': message['params']['processHandle'], 'exitCode': 0,
                            'stdout': 'status=pending count=1 max_id=1\n', 'stderr': ''}})
                    elif method == 'turn/start':
                        turns.append(message['params'])
                        time.sleep(0.05)
                        self.send({'jsonrpc': '2.0', 'method': 'turn/completed', 'params': {
                            'threadId': 'thread-1', 'turn': {'id': 'turn-1'}}})

        # Node maps native drive paths to /c/...; use a non-/tmp project so
        # this transport check does not depend on an MSYS mount alias.
        scratch = ROOT / 'run'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='hook-bridge-test-', dir=scratch) as project_dir:
            self.project = Path(project_dir)
            self.enable()
            (self.skill / 'scripts/session-start.sh').write_text(self.real_start, encoding='utf-8', newline='\n')
            def bash(code):
                result = subprocess.run([BASH, '--noprofile', '--norc', '-c', 'set -e; ' + code,
                                         '_', str(self.skill), str(self.project)],
                                        capture_output=True, encoding='utf-8', env=self.env, timeout=40)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            bash('bash "$1/scripts/internal/init-db.sh"; '
                 'project=$(cd "$2" && pwd); '
                 'bash "$1/scripts/join.sh" team alice codex "$project"; '
                 'bash "$1/scripts/join.sh" team bob codex "$project"; '
                 'SKILL_DIR=$(cd "$1" && pwd); source "$SKILL_DIR/scripts/lib/role-session.sh"; '
                 'agmsg_role_session_record team alice thread-1 "$project" codex; '
                 'bash "$1/scripts/send.sh" team bob alice queue-to-bridge-message')
            with socketserver.ThreadingTCPServer(('127.0.0.1', 0), AppServer) as server:
                server.daemon_threads = True
                thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
                thread.start()
                try:
                    self.seat_record(port=server.server_address[1])
                    self.enqueue(AGMSG_CODEX_SEAT_KEY='123.456.7')
                    self.dispatch()
                    fields = (self.skill / 'run/codex-bridge-request.123.456.7').read_text(encoding='utf-8').strip().split('\t')
                    self.assertEqual(fields[3:], ['team', 'alice'])
                    result = subprocess.run([shutil.which('node'), str(self.skill / 'scripts/drivers/types/codex/codex-bridge.js'),
                                             '--project', str(self.project), '--pair', '\t'.join(fields[3:]),
                                             '--thread', fields[1], '--app-server', fields[2], '--inline-inbox', '--max-wakes', '1'],
                                            capture_output=True, encoding='utf-8', env=dict(self.env, GIT_BASH=BASH), timeout=35)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(len(turns), 1)
                    self.assertEqual(turns[0]['threadId'], 'thread-1')
                    self.assertIn('queue-to-bridge-message', turns[0]['input'][0]['text'])
                finally:
                    server.shutdown()
                    thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
