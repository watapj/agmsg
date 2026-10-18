"""Exercise the real bridge state machine without live sessions or messages."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = r'''
const assert = require('assert/strict'), fs=require('fs'), path=require('path');
process.env.GIT_BASH=process.execPath;
const {CodexBridge}=require('./scripts/drivers/types/codex/codex-bridge');
const dir=process.argv[1], b=Object.create(CodexBridge.prototype);
let starts=0, acks=[], failStart=false, failAck=false;
const message={team:'team',to:'alice',from:'bob',id:'m1',at:'now',body:'hello 日本語'};
Object.assign(b,{opts:{inlineInbox:true,project:dir,type:'codex'},
  identities:[{team:'team',name:'alice'}],identity:{team:'team',name:'alice'},
  threadId:'thread',pendingWake:true,threadIdle:true,turnActive:false,
  deliveryFile:path.join(dir,'delivery.json'),
  startTurnWatchdog(){},clearTurnWatchdog(){},async armWatch(){},
  client:{async request(method){assert.equal(method,'turn/start');starts++;
    assert.equal(acks.length,0); if(failStart)throw new Error('rejected');}},
  async runInboxHelper(args){
    if(args[0].endsWith('eligible-pairs.sh'))return 'team\talice\n';
    if(args.includes('--snapshot'))return JSON.stringify(message)+'\n';
    assert(args.includes('--ack'));
    if(failAck)throw new Error('receipt write refused');
    acks.push(args.slice(4)); return '';
  }});
'''


class DeliveryReceiptTests(unittest.TestCase):
    def run_js(self, body):
        with tempfile.TemporaryDirectory(prefix="agmsg-receipt-") as directory:
            result = subprocess.run(
                ["node", "-e", BASE + "\n(async()=>{\n" + body +
                 "\n})().catch(e=>{console.error(e);process.exitCode=1});", directory],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ack_only_after_acceptance_and_only_snapshot_ids(self):
        self.run_js("""
await b.tryStartTurn(); assert.equal(starts,1);
assert.deepEqual(acks,[['m1']]); assert(!fs.existsSync(b.deliveryFile));
assert(b.inlineInboxText.includes('[id=m1]'));
""")

    def test_rejected_start_preserves_unread_and_blocks_automatic_replay(self):
        self.run_js("""
failStart=true; await assert.rejects(b.tryStartTurn(),/rejected/);
assert.equal(acks.length,0);
assert.equal(JSON.parse(fs.readFileSync(b.deliveryFile)).phase,'submitting');
await assert.rejects(b.resumeDelivery(),/outcome unknown/);
await assert.rejects(b.tryStartTurn(),/unresolved delivery/);
assert.equal(starts,1);
""")

    def test_ack_failure_recovers_without_another_turn(self):
        self.run_js("""
failAck=true; await assert.rejects(b.tryStartTurn(),/receipt write refused/);
assert.equal(JSON.parse(fs.readFileSync(b.deliveryFile)).phase,'accepted');
failAck=false; await b.resumeDelivery();
assert.equal(starts,1); assert.deepEqual(acks,[['m1']]);
assert(!fs.existsSync(b.deliveryFile));
""")

    def test_changed_owner_cannot_ack(self):
        self.run_js("""
b.eligibleInboxPairs=async()=>new Set();
b.inboxBatch=[{team:'team',name:'alice',ids:['m1']}];
b.writeDelivery('accepted');
await assert.rejects(b.ackDelivery(),/ownership changed/);
assert.equal(acks.length,0); assert(fs.existsSync(b.deliveryFile));
""")

    def test_async_wakes_do_not_start_two_turns(self):
        self.run_js("""
const reader=b.readInboxForPrompt.bind(b);
b.readInboxForPrompt=async()=>{await new Promise(r=>setTimeout(r,50));return reader();};
await Promise.all([b.tryStartTurn(),b.tryStartTurn()]);
assert.equal(starts,1); assert.equal(acks.length,1);
""")

    def test_user_turn_started_during_snapshot_defers_unread_delivery(self):
        self.run_js("""
const reader=b.readInboxForPrompt.bind(b);
b.readInboxForPrompt=async()=>{const text=await reader();b.turnActive=true;b.threadIdle=false;return text;};
await b.tryStartTurn();
assert.equal(starts,0);assert.equal(acks.length,0);assert(b.pendingWake);
assert(!fs.existsSync(b.deliveryFile));
""")

    def test_receipt_for_another_thread_is_refused(self):
        self.run_js("""
b.inboxBatch=[];b.writeDelivery('accepted');b.threadId='other';
await assert.rejects(b.resumeDelivery(),/identity mismatch/);
assert.equal(acks.length,0);
""")

    def test_inbox_helper_does_not_block_event_loop(self):
        self.run_js("""
b.runInboxHelper=CodexBridge.prototype.runInboxHelper;
let responsive=false;
setTimeout(()=>{responsive=true;},20);
const result=await b.runInboxHelper(['-e',"setTimeout(()=>process.stdout.write('ok'),150)"]);
assert(responsive);assert.equal(result,'ok');
""")

    def test_shutdown_cancels_inbox_helper(self):
        self.run_js("""
b.runInboxHelper=CodexBridge.prototype.runInboxHelper;
b.client.stop=()=>{}; b.cleanupMeta=()=>{};
const failed=assert.rejects(b.runInboxHelper(['-e','setTimeout(()=>{},10000)']),/inbox helper failed/);
await new Promise(r=>setTimeout(r,100));
await b.shutdown(); await failed;
""")


@unittest.skipUnless(os.name == "nt", "Windows named pipe recovery")
class ControlRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agmsg-recovery-")
        self.root = Path(self.temp.name)
        self.project = self.root / "project o'brien 日本語"
        self.project.mkdir()
        self.control = self.root / "scripts/drivers/types/codex/monitor-control.js"
        self.control.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "scripts/drivers/types/codex/monitor-control.js", self.control)
        (self.root / "run").mkdir()
        self.file = self.root / "run/codex-bridge.team.alice.delivery.json"
        self.receipt = dict(version=1, phase="submitting", thread="thread",
                            project=str(self.project), identities=[dict(team="team", name="alice")],
                            batches=[dict(team="team", name="alice", ids=["m1"])])
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        self.file.write_text(json.dumps(self.receipt), encoding="utf-8")

    def call(self, action, thread="thread"):
        return subprocess.run(["node", str(self.control), action, str(self.project), "team", "alice", thread],
                              capture_output=True, text=True, encoding="utf-8", timeout=10)

    def test_recovery_requires_exact_thread(self):
        result = self.call("delivery-retry", "other")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(self.file.read_text()), self.receipt)

    def test_unknown_can_be_explicitly_retried(self):
        result = self.call("delivery-retry")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.file.exists())

    def test_accepted_is_never_retried(self):
        result = self.call("delivery-accept")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.call("delivery-retry")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(self.file.read_text())["phase"], "accepted")


if __name__ == "__main__":
    unittest.main()
