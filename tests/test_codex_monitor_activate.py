"""Windows current-session monitor tests. No installed settings are changed."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = r"C:\Program Files\Git\bin\bash.exe"


@unittest.skipUnless(os.name == "nt", "Windows named pipes required")
class ActivateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agmsg-activate-")
        self.root = Path(self.temp.name)
        self.skill = self.root / "skill o'brien $x & [日本語]"
        shutil.copytree(ROOT / "scripts", self.skill / "scripts")
        self.driver = self.skill / "scripts/drivers/types/codex"
        self.project = self.root / "project o'brien $(touch INJECTED) & [日本語]"
        self.project.mkdir()
        self.log = self.root / "rpc.jsonl"
        self.thread = "11111111-1111-7111-8111-111111111111"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("AGMSG_", "CODEX_"))}
        self.env.update(GIT_BASH=BASH, CODEX_THREAD_ID=self.thread,
                        TEST_PROJECT=str(self.project), TEST_LOG=str(self.log))
        (self.skill / "scripts/identities.sh").write_text("printf 'team\\talice\\n'\n", encoding="utf-8")
        (self.driver / "codex-monitor-activate.sh").write_text("printf 'team\\talice\\n'\n", encoding="utf-8")
        self.fake = self.root / "server.js"
        self.fake.write_text(r'''
const fs = require('fs');
const crypto = require('crypto');
let buffer=Buffer.alloc(0),upgraded=false;
function send(message){
 const data=Buffer.from(JSON.stringify(message)),h=Buffer.alloc(data.length<126?2:4);
 h[0]=0x81;if(h.length===2)h[1]=data.length;else{h[1]=126;h.writeUInt16BE(data.length,2);}
 process.stdout.write(Buffer.concat([h,data]));
}
process.stdin.on('data', chunk=>{
 buffer=Buffer.concat([buffer,chunk]);
 if(!upgraded){
  const i=buffer.indexOf('\r\n\r\n');if(i<0)return;
  const key=buffer.subarray(0,i).toString().match(/Sec-WebSocket-Key: (.*)\r\n/i)[1].trim();
  const accept=crypto.createHash('sha1').update(key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
  process.stdout.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+'\r\n\r\n');
  buffer=buffer.subarray(i+4);upgraded=true;
 }
 while(buffer.length>=2){
  let n=buffer[1]&127,o=2;if(n===126){if(buffer.length<4)return;n=buffer.readUInt16BE(2);o=4;}
  if(buffer.length<o+4+n)return;
  const mask=buffer.subarray(o,o+4),data=buffer.subarray(o+4,o+4+n);
  for(let j=0;j<n;j++)data[j]^=mask[j%4];
  buffer=buffer.subarray(o+4+n);handle(data.toString());
 }
});
function handle(line){
  const m = JSON.parse(line);
  fs.appendFileSync(process.env.TEST_LOG, JSON.stringify(m)+'\n');
  if (!m.id) return;
  let result = {};
  if (m.method === 'thread/loaded/list') result = {data: [process.env.CODEX_THREAD_ID]};
  if (m.method === 'thread/read') result = {thread: {cwd: process.env.TEST_PROJECT}};
  if (m.method === 'thread/resume') result = {thread: {id: process.env.CODEX_THREAD_ID, status: {type:'active'}}};
  if (process.env.TEST_WAKE && m.method === 'thread/resume') result.thread.status.type='idle';
  if (process.env.TEST_UNLOADED && m.method === 'thread/loaded/list') result = {data: []};
  if (process.env.TEST_WRONG_PROJECT && m.method === 'thread/read') result = {thread: {cwd: process.env.TEMP}};
  if (process.env.TEST_BAD_RESUME && m.method === 'thread/resume') {
    send({id:m.id,error:{code:-32600,message:'already has an active writer'}}); return;
  }
  send({id:m.id,result});
  if(process.env.TEST_WAKE && m.method==='process/spawn')setTimeout(()=>send({method:'process/exited',params:{processHandle:m.params.processHandle,exitCode:0,stdout:'status=pending count=1 max_id=fixture\n'}}),100);
}
''', encoding="utf-8")
        # The existing stdio test hook uses /bin/sh on POSIX; for Windows use
        # daemon:// through a fixed fake codex executable on Git Bash's PATH.
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "codex").write_text('#!/usr/bin/env bash\nexec node "$TEST_SERVER"\n', encoding="utf-8")
        search_path = next(v for k, v in self.env.items() if k.lower() == "path")
        self.env = {k: v for k, v in self.env.items() if k.lower() != "path"}
        self.env.update(TEST_SERVER=str(self.fake), PATH=str(self.bin) + os.pathsep + search_path)
        self.processes = []

    def tearDown(self):
        try:
            self.run_bridge("--stop-attached", check=False)
        finally:
            for p in self.processes:
                if p.poll() is None:
                    p.kill()
                p.communicate(timeout=10)
            # Stop acknowledgement precedes shutdown's pipe cleanup.
            for _ in range(100):
                if not list((self.skill / "run").glob("*.pid")):
                    break
                time.sleep(.05)
            self.temp.cleanup()

    def argv(self, *args):
        return ["node", str(self.driver / "codex-bridge.js"), "--project", str(self.project),
                "--pair", "team\talice", "--app-server", "daemon://", "--thread", "current", *args]

    def run_bridge(self, *args, check=True, env=None):
        r = subprocess.run(self.argv(*args), env=env or self.env, capture_output=True,
                           encoding="utf-8", timeout=55)
        if check:
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout + self.bridge_log())
        return r

    def bridge_log(self):
        p = self.skill / "run/codex-bridge.team.alice.log"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def requests(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def test_activate_waits_until_armed_and_repeat_reuses_same_process(self):
        first = self.run_bridge("--activate-current")
        second = self.run_bridge("--activate-current")
        self.assertIn("Codex monitor receiving:", first.stdout)
        self.assertEqual(first.stdout, second.stdout)
        calls = self.requests()
        self.assertEqual(sum(m["method"] == "process/spawn" for m in calls), 1)
        self.assertFalse(any(m["method"] in ("thread/start", "turn/start") for m in calls))
        self.assertFalse((self.project / "INJECTED").exists())

    def test_unloaded_current_thread_fails_without_resume_or_watch(self):
        env = dict(self.env, TEST_UNLOADED="1")
        self.assertNotEqual(self.run_bridge("--activate-current", check=False, env=env).returncode, 0)
        self.assertNotIn("thread/resume", [m["method"] for m in self.requests()])

    def test_wrong_project_fails_without_resume_or_watch(self):
        env = dict(self.env, TEST_WRONG_PROJECT="1")
        self.assertNotEqual(self.run_bridge("--activate-current", check=False, env=env).returncode, 0)
        self.assertNotIn("process/spawn", [m["method"] for m in self.requests()])

    def test_resume_error_is_not_reported_as_receiving(self):
        env = dict(self.env, TEST_BAD_RESUME="1")
        result = self.run_bridge("--activate-current", check=False, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("receiving:", result.stdout)
        self.assertIn("active writer", self.bridge_log())

    def test_another_thread_cannot_reuse_the_same_role(self):
        self.run_bridge("--activate-current")
        env = dict(self.env, CODEX_THREAD_ID="22222222-2222-7222-8222-222222222222")
        result = self.run_bridge("--activate-current", env=env, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("receiving:", result.stdout)
        self.assertEqual(sum(m["method"] == "process/spawn" for m in self.requests()), 1)

    def test_control_status_and_repair_use_live_native_pid(self):
        result = self.run_bridge("--activate-current")
        native_pid = result.stdout.split("pid=")[1].strip()
        pidfile = self.skill / "run/codex-bridge.team.alice.pid"
        pidfile.write_text("99999999\n", encoding="utf-8")
        command = ["node", str(self.driver / "monitor-control.js")]
        args = [str(self.project), "team", "alice", self.thread]
        status = subprocess.run(command + ["status"] + args, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(status.stdout.strip(), native_pid)
        self.assertEqual(pidfile.read_text(encoding="utf-8").strip(), "99999999")
        wrong = subprocess.run(command + ["repair"] + args[:-1] + ["wrong-thread"],
                               env=self.env, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(wrong.returncode, 0)
        self.assertEqual(pidfile.read_text(encoding="utf-8").strip(), "99999999")
        repaired = subprocess.run(command + ["repair"] + args, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        self.assertEqual(pidfile.read_text(encoding="utf-8").strip(), native_pid)
        self.assertEqual(sum(m["method"] == "process/spawn" for m in self.requests()), 1)

    def test_control_repair_rejects_conflicting_metadata(self):
        self.run_bridge("--activate-current")
        pidfile = self.skill / "run/codex-bridge.team.alice.pid"
        meta = self.skill / "run/codex-bridge.team.alice.meta"
        original = meta.read_text(encoding="utf-8")
        try:
            meta.write_text(original.replace("type=codex", "type=claude-code"), encoding="utf-8")
            result = subprocess.run(["node", str(self.driver / "monitor-control.js"), "repair",
                                     str(self.project), "team", "alice", self.thread], env=self.env,
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(meta.read_text(encoding="utf-8"), original.replace("type=codex", "type=claude-code"))
        finally:
            meta.write_text(original, encoding="utf-8")

    def test_control_stop_requires_exact_thread_and_stops_subscription_only(self):
        self.run_bridge("--activate-current")
        command = ["node", str(self.driver / "monitor-control.js"), "stop",
                   str(self.project), "team", "alice"]
        wrong = subprocess.run(command + ["another-thread"], env=self.env,
                               capture_output=True, text=True, timeout=10)
        self.assertNotEqual(wrong.returncode, 0)
        self.assertTrue((self.skill / "run/codex-bridge.team.alice.pid").exists())
        stopped = subprocess.run(command + [self.thread], env=self.env,
                                 capture_output=True, text=True, timeout=50)
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertFalse((self.skill / "run/codex-bridge.team.alice.pid").exists())
        methods = [m["method"] for m in self.requests()]
        self.assertIn("process/kill", methods)
        self.assertNotIn("turn/interrupt", methods)
        self.assertNotIn("thread/archive", methods)

    def test_poke_mode_retires_current_monitor_and_reports_ready(self):
        self.run_bridge("--activate-current")
        env = dict(self.env, TEST_SKILL=str(self.skill), TEST_THREAD=self.thread)
        record = subprocess.run([BASH, "-lc", '''
SKILL_DIR="$(cygpath -u "$TEST_SKILL")"
. "$SKILL_DIR/scripts/lib/role-session.sh"
agmsg_role_session_record team alice "$TEST_THREAD" "$(cygpath -u "$TEST_PROJECT")" codex owner
bash "$SKILL_DIR/scripts/delivery.sh" set poke codex "$(cygpath -u "$TEST_PROJECT")"
'''], env=env, capture_output=True, encoding="utf-8", timeout=90)
        self.assertEqual(record.returncode, 0, record.stderr + record.stdout)
        self.assertIn("raw body", record.stdout)
        self.assertFalse((self.skill / "run/codex-bridge.team.alice.pid").exists())
        self.assertTrue(any(p.read_text(encoding="utf-8").strip() == "ready"
                            for p in (self.skill / "run").glob("delivery-poke.codex.*")))
        hooks = json.loads((self.project / ".codex/hooks.json").read_text(encoding="utf-8"))
        self.assertFalse(hooks.get("hooks"))
        self.assertNotIn("turn/interrupt", [m["method"] for m in self.requests()])

    def test_claude_poke_transition_waits_for_native_path_watcher_to_exit(self):
        watcher = self.skill / "scripts/watch.sh"
        watcher.write_text(r'''#!/usr/bin/env bash
root="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$root/run"
trap 'rm -f "$root/run/watch.fixture.pid" "$root/run/watch.fixture.filter"; exit 0' TERM INT EXIT
printf 'alice\n%s\n%s\n' "$1" "$$" > "$root/run/watch.fixture.filter"
printf '%s\n' "$$" > "$root/run/watch.fixture.pid"
while :; do sleep .2; done
''', encoding="utf-8")
        child = subprocess.Popen([BASH, str(watcher), str(self.project), "claude-code"],
                                 env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(child)
        for _ in range(200):
            if (self.skill / "run/watch.fixture.pid").exists():
                break
            time.sleep(.05)
        self.assertTrue((self.skill / "run/watch.fixture.pid").exists())
        env = dict(self.env, TEST_SKILL=str(self.skill))
        result = subprocess.run([BASH, "-lc", '''
skill="$(cygpath -u "$TEST_SKILL")"
bash "$skill/scripts/delivery.sh" set poke claude-code "$(cygpath -u "$TEST_PROJECT")"
'''], env=env, capture_output=True, encoding="utf-8", timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(child.wait(timeout=15), 0)
        self.assertFalse((self.skill / "run/watch.fixture.pid").exists())
        self.assertTrue(any(p.read_text(encoding="utf-8").strip() == "ready"
                            for p in (self.skill / "run").glob("delivery-poke.claude-code.*")))

    def test_role_reassignment_stops_the_attached_monitor(self):
        self.run_bridge("--activate-current")
        (self.driver / "codex-monitor-activate.sh").write_text("exit 1\n", encoding="utf-8")
        for _ in range(140):
            if not (self.skill / "run/codex-bridge.team.alice.pid").exists():
                break
            time.sleep(.05)
        self.assertFalse((self.skill / "run/codex-bridge.team.alice.pid").exists())
        self.assertIn("no longer owns this role", self.bridge_log())

    def test_real_activation_helper_resolves_recorded_role(self):
        shutil.copyfile(ROOT / "scripts/drivers/types/codex/codex-monitor-activate.sh",
                        self.driver / "codex-monitor-activate.sh")
        code = ('SKILL_DIR=$(cd "$1" && pwd); source "$SKILL_DIR/scripts/lib/resolve-project.sh"; '
                'source "$SKILL_DIR/scripts/lib/role-session.sh"; '
                'agmsg_role_session_record team alice "$CODEX_THREAD_ID" "$(agmsg_canonical_path "$2")" codex; '
                'bash "$SKILL_DIR/scripts/delivery.sh" set monitor codex "$2"')
        dispatcher = self.skill / "scripts/windows/hook-dispatcher.ps1"
        try:
            result = subprocess.run([BASH, "-c", code, "_", str(self.skill), str(self.project)],
                                env=self.env, capture_output=True, encoding="utf-8", timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr + self.bridge_log())
            self.assertIn("receiving:", result.stdout)
            self.assertIn("hook-enqueue.ps1", (self.project / ".codex/hooks.json").read_text(encoding="utf-8"))
            result = subprocess.run([BASH, "-c", 'bash "$1/scripts/delivery.sh" set turn codex "$2"',
                                     "_", str(self.skill), str(self.project)], env=self.env,
                                    capture_output=True, encoding="utf-8", timeout=40)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((self.skill / "run/codex-bridge.team.alice.pid").exists())
            for mode in ("monitor", "off"):
                result = subprocess.run([BASH, "-c", 'bash "$1/scripts/delivery.sh" set "$3" codex "$2"',
                                         "_", str(self.skill), str(self.project), mode], env=self.env,
                                        capture_output=True, encoding="utf-8", timeout=55)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr + self.bridge_log())
            self.assertFalse((self.skill / "run/codex-bridge.team.alice.pid").exists())
        finally:
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-File", str(dispatcher), "-Action", "Stop"], env=self.env, capture_output=True, timeout=10)
            time.sleep(1.2)

    def test_activated_bridge_delivers_to_exact_current_thread(self):
        (self.driver / "eligible-pairs.sh").write_text("printf 'team\\talice\\n'\n", encoding="utf-8")
        (self.skill / "scripts/inbox.sh").write_text("printf 'isolated fixture message\\n'\n", encoding="utf-8")
        env = dict(self.env, TEST_WAKE="1")
        self.run_bridge("--activate-current", "--inline-inbox", "--max-wakes", "1", env=env)
        for _ in range(100):
            turns = [m for m in self.requests() if m["method"] == "turn/start"]
            if turns:
                break
            time.sleep(.05)
        self.assertEqual(len(turns), 1, self.bridge_log())
        self.assertEqual(turns[0]["params"]["threadId"], self.thread)
        self.assertIn("isolated fixture message", turns[0]["params"]["input"][0]["text"])

    def test_concurrent_activation_arms_only_one_watcher(self):
        for _ in range(2):
            self.processes.append(subprocess.Popen(self.argv("--activate-current"), env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8"))
        results = [(p.communicate(timeout=55), p.returncode) for p in self.processes]
        self.assertTrue(any(code == 0 for _, code in results), str(results) + self.bridge_log())
        self.assertEqual(sum(m["method"] == "process/spawn" for m in self.requests()), 1)

    def test_stop_closes_monitor_and_allows_activation_again(self):
        self.run_bridge("--activate-current")
        self.run_bridge("--stop-attached")
        self.assertFalse((self.skill / "run/codex-bridge.team.alice.pid").exists())
        self.run_bridge("--activate-current")

    @unittest.skipUnless(os.environ.get("AGMSG_TEST_REAL_CODEX"), "set AGMSG_TEST_REAL_CODEX to native codex.exe")
    def test_real_app_server_existing_thread_and_native_process_spawn(self):
        self.run_real_server(False)

    @unittest.skipUnless(os.environ.get("AGMSG_TEST_REAL_CODEX"), "set AGMSG_TEST_REAL_CODEX to native codex.exe")
    def test_real_default_control_socket_and_native_proxy(self):
        self.run_real_server(True)

    def run_real_server(self, daemon):
        # A separate server/home/thread; never attach to the user's real session
        # or use model tokens. A first turn against this unauthenticated home
        # fails locally, but creates the rollout needed by thread/read.
        # Let Codex create its own protected socket directory. Never precreate
        # it with inherited permissions or change any existing directory ACL.
        if daemon:
            private = tempfile.TemporaryDirectory(prefix="agmsg-codex-test-", dir=Path.home())
            self.addCleanup(private.cleanup)
            home = Path(private.name) / "codex-home"
        else:
            home = self.root / "codex-home"
        home.mkdir(mode=0o700)
        self.env["CODEX_HOME"] = str(home)
        self.env["TEST_CODEX_BIN"] = os.environ["AGMSG_TEST_REAL_CODEX"]
        self.env["TEST_BRIDGE_PATH"] = str(self.driver / "codex-bridge.js")
        self.env["TEST_REAL_DAEMON"] = "1" if daemon else ""
        if daemon:
            (self.bin / "codex").write_text('#!/usr/bin/env bash\nexec "$TEST_CODEX_BIN" "$@"\n', encoding="utf-8")
        (self.driver / "watch-once.sh").write_text("#!/usr/bin/env bash\nsleep 60\nexit 2\n", encoding="utf-8")
        harness = self.root / "real-server.js"
        harness.write_text(r'''
const {spawn,spawnSync} = require('child_process');
const readline = require('readline');
const path = require('path');
const {createAppServerClient}=require(process.env.TEST_BRIDGE_PATH);
const daemon = !!process.env.TEST_REAL_DAEMON;
const sock = path.join(process.env.CODEX_HOME,'app-server-control','app-server-control.sock');
const server = spawn(process.env.TEST_CODEX_BIN, ['app-server','--listen',daemon?'unix://'+sock:'ws://127.0.0.1:0'], {stdio:['ignore','ignore','pipe']});
let client, started = false, diagnostics = [];
server.on('exit',code=>{if(!started)console.log(JSON.stringify({error:'server exit '+code,diagnostics}));});
async function start(url){
  if(started)return;
  started = true;
  try {
    client=createAppServerClient({appServer:url,connectTimeoutMs:5000,requestTimeoutMs:10000});
    client.start();await client.ready();
    await client.request('initialize',{clientInfo:{name:'agmsg-activation-test',version:'0'},capabilities:{experimentalApi:true}});
    client.notify('initialized');
    const r = await client.request('thread/start',{cwd:process.env.TEST_PROJECT,ephemeral:false});
    await client.request('turn/start',{threadId:r.thread.id,input:[{type:'text',text:'activation fixture',text_elements:[]}]});
    await new Promise(resolve=>setTimeout(resolve,500));
    console.log(JSON.stringify({url,thread:r.thread.id}));
  } catch(e) {console.log(JSON.stringify({error:e.message}));}
}
readline.createInterface({input:server.stderr}).on('line', line=>{
 diagnostics.push(line);diagnostics=diagnostics.slice(-3);
 const match=line.match(/ws:\/\/127\.0\.0\.1:(\d+)/);
 if(!daemon&&match)start('ws://127.0.0.1:'+match[1]);
});
if(daemon)setTimeout(()=>start('daemon://'),1000);
process.stdin.resume();
process.stdin.on('end',()=>{
  if(client)client.stop();
  spawnSync('taskkill.exe',['/PID',String(server.pid),'/T','/F'],{stdio:'ignore'});
  process.exit(0);
});
process.on('exit',()=>server.kill());
setTimeout(()=>{server.kill();process.exit(1);},90000).unref();
''', encoding="utf-8")
        p = subprocess.Popen(["node", str(harness)], env=self.env, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        try:
            state = json.loads(p.stdout.readline())
            self.assertNotIn("error", state)
            self.env["CODEX_THREAD_ID"] = state["thread"]
            args = self.argv("--activate-current")
            args[args.index("daemon://")] = state["url"]
            r = subprocess.run(args, env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=55)
            self.assertEqual(r.returncode, 0, r.stderr + self.bridge_log())
            self.assertIn("receiving:", r.stdout)
            self.run_bridge("--stop-attached")
        finally:
            p.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
