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

    def test_user_turn_during_ack_is_kept_active(self):
        self.run_js("""
let rearmed=0;
b.armWatch=async()=>{rearmed++;};
b.client.request=async()=>{starts++;b.onTurnStarted({threadId:'thread',turn:{id:'delivery'}});};
const helper=b.runInboxHelper.bind(b);
b.runInboxHelper=async args=>{
  if(args.includes('--ack')){
    assert.equal(b.startInFlight,false);
    await b.onTurnCompleted({threadId:'thread',turn:{id:'delivery'}});
    b.onTurnStarted({threadId:'thread',turn:{id:'user'}});
  }
  return helper(args);
};
await b.tryStartTurn();
assert.equal(b.activeTurnId,'user');assert(b.turnActive);assert(!b.threadIdle);
assert.equal(rearmed,0);assert.equal(acks.length,1);
await b.onTurnCompleted({threadId:'thread',turn:{id:'delivery'}});
assert(b.turnActive);assert.equal(rearmed,0);
""")

    def test_deferred_end_before_rpc_reply_cannot_end_later_user_turn(self):
        self.run_js("""
b.client.request=async()=>{
  starts++;b.onTurnStarted({threadId:'thread',turn:{id:'delivery'}});
  await b.onTurnCompleted({threadId:'thread',turn:{id:'delivery'}});
  b.onTurnStarted({threadId:'thread',turn:{id:'user'}});
};
await b.tryStartTurn();
assert.equal(b.inFlightTurnId,'delivery');assert.equal(b.activeTurnId,'user');
assert(b.turnActive);assert(!b.threadIdle);assert.equal(acks.length,1);
""")

    def test_turn_ending_during_ack_rearms_only_after_ack(self):
        self.run_js("""
let rearmed=0;
b.armWatch=async()=>{assert.equal(acks.length,1);assert(!b.deliveryInFlight);rearmed++;};
b.client.request=async()=>{starts++;b.onTurnStarted({threadId:'thread',turn:{id:'delivery'}});};
const helper=b.runInboxHelper.bind(b);
b.runInboxHelper=async args=>{
  if(args.includes('--ack')){
    await b.onTurnCompleted({threadId:'thread',turn:{id:'delivery'}});
    assert.equal(rearmed,0);
  }
  return helper(args);
};
await b.tryStartTurn();assert.equal(rearmed,1);assert(b.threadIdle);
""")

    def test_pending_wake_during_ack_is_not_dropped(self):
        self.run_js("""
let snapshots=0,rearmed=0;
b.armWatch=async()=>{rearmed++;};
b.client.request=async()=>{starts++;b.onTurnStarted({threadId:'thread',turn:{id:'delivery'}});};
const helper=b.runInboxHelper.bind(b);
b.runInboxHelper=async args=>{
  if(args.includes('--snapshot') && ++snapshots>1)return '';
  if(args.includes('--ack')){
    b.pendingWake=true;
    await b.onTurnCompleted({threadId:'thread',turn:{id:'delivery'}});
  }
  return helper(args);
};
await b.tryStartTurn();
assert.equal(starts,1);assert.equal(snapshots,2);assert.equal(rearmed,1);assert(!b.pendingWake);
""")

    def test_shutdown_cancellation_cannot_interrupt_stop_response(self):
        self.run_js("""
let rejectPending,handled=0;
b.shutdown=()=>{throw new Error('shutdown was re-entered by cancellation');};
const handler=b.clientHandler('fixture',()=>new Promise((resolve,reject)=>{rejectPending=reject;}));
handler({});b.stopping=true;
rejectPending(new Error('intentional helper cancellation'));
await new Promise(setImmediate);
b.failClientHandler('fixture',new Error('late error'));
b.clientHandler('turn/started',()=>{handled++;})({});
assert.equal(handled,0);
""")

    def test_inbox_helper_does_not_block_event_loop(self):
        self.run_js("""
b.runInboxHelper=CodexBridge.prototype.runInboxHelper;
let responsive=false;
setTimeout(()=>{responsive=true;},20);
const result=await b.runInboxHelper(['-e',"setTimeout(()=>process.stdout.write('ok'),150)"]);
assert(responsive);assert.equal(result,'ok');
""")

    def test_pre_submission_helper_failure_pauses_and_retry_delivers_once(self):
        self.run_js("""
const helper=b.runInboxHelper.bind(b);let failures=1;
b.monitorInitialized=true;b.monitorReady=true;
b.runInboxHelper=async args=>{
 if(args.includes('--snapshot') && failures-- > 0){
  const e=new Error('temporary read failure');e.inboxHelperFailure=true;throw e;
 }
 return helper(args);
};
await b.tryStartTurn();
assert.equal(starts,0);assert.equal(acks.length,0);assert(b.pendingWake);
assert(!fs.existsSync(b.deliveryFile));assert(b.deliveryRetryTimer);
assert.equal(b.runtimeStatus().ready,false);assert.equal(b.runtimeStatus().phase,'retry-wait');
await b.tryStartTurn();assert.equal(starts,0);
clearTimeout(b.deliveryRetryTimer);b.deliveryRetryTimer=null;
await b.tryStartTurn();
assert.equal(starts,1);assert.equal(acks.length,1);assert(b.runtimeStatus().ready);
assert.equal(b.deliveryRetryCount,0);assert.equal(b.runtimeStatus().lastError,'');
""")

    def test_pre_submission_retries_stop_after_three_retries(self):
        self.run_js("""
b.readInboxForPrompt=async()=>{const e=new Error('read blocked');e.inboxHelperFailure=true;throw e;};
for(let n=0;n<4;n++){
 await b.tryStartTurn();
 if(b.deliveryRetryTimer){clearTimeout(b.deliveryRetryTimer);b.deliveryRetryTimer=null;}
}
assert(b.deliveryRetryExhausted);assert.equal(b.runtimeStatus().phase,'paused');
assert(!b.runtimeStatus().ready);assert(b.pendingWake);
await b.tryStartTurn();assert.equal(b.deliveryRetryCount,4);assert.equal(starts,0);
""")

    def test_current_thread_binding_is_checked_again_after_snapshot(self):
        self.run_js("""
b.opts.attachCurrent=true;b.verifyThreadOnServer=async()=>{};
const helper=b.runInboxHelper.bind(b);let checks=0;
b.runInboxHelper=async args=>{
 if(args[0].endsWith('eligible-pairs.sh')){
  assert.equal(args[args.indexOf('--thread')+1],'thread');
  if(++checks===2){const e=new Error('thread role changed');e.inboxHelperFailure=true;throw e;}
 }
 return helper(args);
};
await b.tryStartTurn();
clearTimeout(b.deliveryRetryTimer);b.deliveryRetryTimer=null;
assert.equal(checks,2);assert.equal(starts,0);assert.equal(acks.length,0);
assert(b.deliveryPaused);assert(!fs.existsSync(b.deliveryFile));
""")

    def test_inline_delivery_keeps_fresh_checks_without_duplicate_role_probe(self):
        self.run_js("""
b.opts.attachCurrent=true;b.monitorInitialized=true;b.roleCheckError='old failure';
let serverChecks=0,roleChecks=0;
b.verifyThreadOnServer=async()=>{serverChecks++;};
b.checkCurrentThread=async()=>{throw new Error('redundant role probe');};
const helper=b.runInboxHelper.bind(b);
b.runInboxHelper=async args=>{
 if(args[0].endsWith('eligible-pairs.sh')){
  assert.equal(args[args.indexOf('--thread')+1],'thread');roleChecks++;
 }
 return helper(args);
};
await b.tryStartTurn();
assert.equal(serverChecks,1);assert.equal(roleChecks,3);
assert.equal(starts,1);assert.equal(acks.length,1);assert.equal(b.roleCheckError,'');
""")

    def test_inline_delivery_refuses_wrong_server_before_reading_inbox(self):
        self.run_js("""
b.opts.attachCurrent=true;
b.verifyThreadOnServer=async()=>{throw new Error('wrong server');};
b.runInboxHelper=async()=>{throw new Error('must not read inbox');};
await assert.rejects(b.tryStartTurn(),/wrong server/);
assert.equal(starts,0);assert.equal(acks.length,0);
""")

    def test_inline_delivery_waits_for_existing_probe_without_another_probe(self):
        self.run_js("""
b.opts.attachCurrent=true;
let finish;
b.currentCheck=new Promise(resolve=>{finish=resolve;});
b.verifyThreadOnServer=async()=>{throw new Error('duplicate server probe');};
const pending=b.tryStartTurn();
await new Promise(resolve=>setTimeout(resolve,10));
assert.equal(starts,0);assert.equal(acks.length,0);
finish();await pending;
assert.equal(starts,1);assert.equal(acks.length,1);
""")

    def test_ack_helper_failure_never_schedules_a_new_turn(self):
        self.run_js("""
const helper=b.runInboxHelper.bind(b);
b.runInboxHelper=async args=>{
 if(args.includes('--ack')){const e=new Error('ack blocked');e.inboxHelperFailure=true;throw e;}
 return helper(args);
};
await assert.rejects(b.tryStartTurn(),/ack blocked/);
assert.equal(starts,1);assert(!b.deliveryRetryTimer);
assert.equal(JSON.parse(fs.readFileSync(b.deliveryFile,'utf8')).phase,'accepted');
""")

    def test_real_helper_timeout_reports_stage_and_elapsed_and_is_retryable(self):
        self.run_js(r"""
b.runInboxHelper=CodexBridge.prototype.runInboxHelper;
await assert.rejects(b.runInboxHelper(['-e','setTimeout(()=>{},10000)'],200),e=>{
 assert(e.inboxHelperFailure);assert.match(e.message,/elapsed=\d+ms/);
 assert.match(e.message,/budget=200ms/);assert.match(e.message,/-e/);return true;
});
assert(!b.inboxHelper);assert(!b.inboxChild);
""")

    def test_scheduled_retry_automatically_starts_a_turn(self):
        self.run_js("""
const savedTimeout=global.setTimeout;let timer;
global.setTimeout=(fn,delay)=>{timer={fn,delay};return timer;};
let blocked=true;
const helper=b.runInboxHelper.bind(b);
b.runInboxHelper=async args=>{
 if(blocked){const e=new Error('temporary');e.inboxHelperFailure=true;throw e;}
 return helper(args);
};
await b.tryStartTurn();assert.equal(timer.delay,5000);assert.equal(starts,0);
blocked=false;timer.fn();await new Promise(setImmediate);
assert.equal(starts,1);assert.equal(acks.length,1);assert(!b.deliveryRetryTimer);
global.setTimeout=savedTimeout;
""")

    def test_shutdown_cancels_a_scheduled_delivery_retry(self):
        self.run_js("""
b.pauseDelivery(new Error('temporary'));
b.client.stop=()=>{};b.cleanupMeta=()=>{};
await b.shutdown();
assert(b.stopping);assert(b.deliveryRetryTimer._destroyed);
""")

    def test_shutdown_cancels_inbox_helper(self):
        self.run_js("""
b.runInboxHelper=CodexBridge.prototype.runInboxHelper;
b.client.stop=()=>{}; b.cleanupMeta=()=>{};
const failed=assert.rejects(b.runInboxHelper(['-e','setTimeout(()=>{},10000)']),/inbox helper failed/);
await new Promise(r=>setTimeout(r,100));
await b.shutdown(); await failed;
""")


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
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), self.receipt)

    def test_unknown_can_be_explicitly_retried(self):
        result = self.call("delivery-retry")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.file.exists())

    def test_accepted_is_never_retried(self):
        result = self.call("delivery-accept")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.call("delivery-retry")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8"))["phase"], "accepted")

    def test_live_monitor_excludes_recovery(self):
        code = r'''
const {spawn}=require('child_process'),assert=require('assert/strict');
const control=require(process.argv[1]);
(async()=>{
 const server=await control.listen(control.address(process.argv[2],process.argv[3],[{team:'team',name:'alice'}]),
   ()=>({ready:true}),async()=>{});
 try {
  const result=await new Promise((resolve,reject)=>{
   const p=spawn(process.execPath,[process.argv[1],'delivery-retry',process.argv[3],'team','alice','thread']);
   p.on('error',reject);p.on('close',resolve);
  });
  assert.notEqual(result,0);
 } finally {await new Promise(resolve=>server.close(resolve));}
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(["node", "-e", code, str(self.control), str(self.root), str(self.project)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), self.receipt)

    def test_posix_cli_recovery_uses_unix_endpoint(self):
        # Exercise POSIX branching on Windows too; actual Unix socket lifecycle
        # is covered separately on POSIX hosts.
        code = r'''
const assert=require('assert/strict'),fs=require('fs'),vm=require('vm'),path=require('path');
const EventEmitter=require('events');
const [file,project,platform,action]=process.argv.slice(1);
let listened=false;
const fakeProcess={platform,getuid:()=>12345,argv:['node',file,action,project,'team','alice','thread']};
const fakeFs={...fs,
 mkdirSync(dir,opts){assert.equal(dir,'/tmp/agmsg-codex-12345');assert.equal(opts.mode,0o700);},
 lstatSync(){return {isDirectory:()=>true,uid:12345,mode:0o700};}};
const fakeNet={createServer(){const server=new EventEmitter();
 server.listen=(socket,ready)=>{assert.match(socket,/^\/tmp\/agmsg-codex-12345\/[a-f0-9]{64}\.sock$/);listened=true;ready();};
 server.close=done=>done();return server;}};
const mod={exports:{}};
function req(name){return name==='fs'?fakeFs:name==='net'?fakeNet:require(name);}
req.main=mod;
vm.runInNewContext(fs.readFileSync(file,'utf8'),{
 require:req,module:mod,process:fakeProcess,__dirname:path.dirname(file),console,
});
setImmediate(()=>{
 assert(listened);assert(!fakeProcess.exitCode);
 const receipt=path.resolve(path.dirname(file),'../../../../run/codex-bridge.team.alice.delivery.json');
 if(action==='delivery-retry')assert(!fs.existsSync(receipt));
 else assert.equal(JSON.parse(fs.readFileSync(receipt,'utf8')).phase,'accepted');
});
'''
        for platform in ("linux", "darwin"):
            for action in ("delivery-retry", "delivery-accept"):
                with self.subTest(platform=platform, action=action):
                    self.write()
                    result = subprocess.run(["node", "-e", code, str(self.control), str(self.project), platform, action],
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipIf(os.name == "nt", "Unix socket lifecycle")
    def test_unix_control_socket_is_removed_on_clean_shutdown(self):
        code = r'''
const assert=require('assert/strict'),fs=require('fs'),control=require(process.argv[1]);
(async()=>{
 const socket=control.address(process.argv[2],process.argv[3],[{team:'team',name:'alice'}]);
 const server=await control.listen(socket,()=>({ready:true}),async()=>{});
 assert(fs.existsSync(socket));
 assert((await control.query(socket)).ready);
 await new Promise(resolve=>server.close(resolve));
 assert(!fs.existsSync(socket));
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(["node", "-e", code, str(self.control), str(self.root), str(self.project)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_posix_address_preserves_case_and_rejects_unsafe_directory(self):
        code = r'''
const assert=require('assert/strict'),fs=require('fs'),vm=require('vm');
const file=process.argv[1],mod={exports:{}};
let stat={isDirectory:()=>true,uid:12345,mode:0o700};
function req(name){return name==='fs'?{
 realpathSync:value=>value,mkdirSync(){},lstatSync:()=>stat,
}:require(name);}
vm.runInNewContext(fs.readFileSync(file,'utf8'),{
 require:req,module:mod,process:{platform:'linux',getuid:()=>12345},
});
const {address}=mod.exports,pair=[{team:'team',name:'alice'}];
assert.notEqual(address('/Root','/Project',pair),address('/root','/project',pair));
for(const unsafe of [{...stat,mode:0o755},{...stat,uid:67890},{...stat,isDirectory:()=>false}]){
 stat=unsafe;assert.throws(()=>address('/root','/project',pair),/unsafe monitor control directory/);
}
'''
        result = subprocess.run(["node", "-e", code, str(self.control)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_control_stop_waits_for_response_flush(self):
        code = r'''
const assert=require('assert/strict'),fs=require('fs'),vm=require('vm'),EventEmitter=require('events');
const mod={exports:{}};
let connection,flushed,stopped=false,request;
const net={createServer(opts,handler){connection=handler;
 const server=new EventEmitter();server.listen=(pipe,ready)=>ready();return server;},
 connect(){const socket=new EventEmitter();socket.setTimeout=()=>{};
  socket.end=()=>{throw new Error('request side closed before reply');};
  socket.write=line=>{request=line;setImmediate(()=>{
   socket.emit('data','{"ready":true}');socket.emit('end');
  });};
  setImmediate(()=>socket.emit('connect'));return socket;}};
function req(name){return name==='net'?net:require(name);}
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{
 require:req,module:mod,process,console,
});
(async()=>{
 const control=mod.exports;
 await control.listen('fixture',()=>({ready:true}),async()=>{},()=>{stopped=true;});
 const socket=new EventEmitter();socket.setTimeout=()=>{};
 socket.end=(response,done)=>{assert.equal(JSON.parse(response).ready,true);flushed=done;};
 connection(socket);socket.emit('data','stop\n');
 await new Promise(setImmediate);
 assert(flushed);assert(!stopped);flushed();assert(stopped);
 assert((await control.query('fixture','stop')).ready);assert.equal(request,'stop\n');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(["node", "-e", code, str(self.control)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
