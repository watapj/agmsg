"use strict";

// Native pipes/sockets serialize monitor startup and receipt recovery. No PID
// selected from a writable file is used to stop a process.
const net = require("net");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

function canonicalPath(value) {
  const real = fs.realpathSync(value);
  return process.platform === "win32" ? real.toLowerCase() : real;
}

function address(root, project, pair) {
  const key = crypto.createHash("sha256")
    .update(JSON.stringify([canonicalPath(root), canonicalPath(project), pair])).digest("hex");
  if (process.platform === "win32") return `\\\\.\\pipe\\agmsg-codex-${key}`;
  // Keep below sockaddr_un's path limit even when the installation path is long.
  const dir = `/tmp/agmsg-codex-${process.getuid()}`;
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const stat = fs.lstatSync(dir);
  if (!stat.isDirectory() || stat.uid !== process.getuid() || (stat.mode & 0o077)) {
    throw new Error(`unsafe monitor control directory: ${dir}`);
  }
  return `${dir}/${key}.sock`;
}

function query(pipe, action = "status", timeoutMs = action === "stop" ? 45000 : 1500) {
  return new Promise((resolve, reject) => {
    const socket = net.connect(pipe);
    let data = "";
    socket.setTimeout(timeoutMs, () => socket.destroy(new Error("monitor control timeout")));
    // The server ends the exchange after its response has been flushed.
    socket.on("connect", () => socket.write(`${action}\n`));
    socket.on("data", (chunk) => {
      data += chunk;
      if (data.length > 8192) socket.destroy(new Error("invalid monitor control response"));
    });
    socket.on("error", reject);
    socket.on("end", () => {
      try { resolve(JSON.parse(data)); } catch (error) { reject(error); }
    });
  });
}

async function listen(pipe, status, stop, stopped = () => {}) {
  const server = net.createServer({ allowHalfOpen: true }, (socket) => {
    socket.setTimeout(1500, () => socket.destroy());
    socket.on("error", () => {});
    let data = "";
    socket.on("data", (chunk) => {
      data += chunk;
      if (data.length > 32) { socket.destroy(); return; }
      if (!data.includes("\n")) return;
      socket.removeAllListeners("data");
      const action = data.trim();
      if (action !== "status" && action !== "stop") { socket.destroy(); return; }
      const result = status();
      if (action === "stop") {
        socket.setTimeout(45000);
        Promise.resolve(stop()).then(() => socket.end(JSON.stringify(result), stopped), () => socket.destroy());
      } else socket.end(JSON.stringify(result));
    });
  });
  await new Promise((resolve, reject) => {
    server.once("error", (error) => {
      if (process.platform !== "win32" && error.code === "EADDRINUSE") {
        error.message += `; control socket: ${pipe}. Stop the subscription first; if the socket is stale after a crash, remove that exact socket and retry. Never remove a live monitor's socket.`;
      }
      reject(error);
    });
    server.listen(pipe, resolve);
  });
  return server;
}

async function activate(pipe, script, args, log, expected) {
  const matches = (s) => s.ready && s.thread === expected.thread && s.appServer === expected.appServer;
  let existing;
  try { existing = await query(pipe); } catch (error) {
    if (error.code !== "ENOENT" && error.code !== "ECONNREFUSED") throw error;
  }
  if (existing) {
    if (!matches(existing)) throw new Error("another monitor owns this role, or monitor is still starting; retry after checking mode status");
    return existing;
  }
  const fd = fs.openSync(log, "a");
  let child;
  try {
    child = spawn(process.execPath, [script, ...args], {
      detached: true, windowsHide: true, stdio: ["ignore", fd, fd, "ipc"],
    });
  } finally { fs.closeSync(fd); }
  return new Promise((resolve, reject) => {
    let done = false;
    const finish = (error, status) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      if (child.connected) child.disconnect();
      child.unref();
      if (error) { child.kill(); reject(error); } else resolve(status);
    };
    const timer = setTimeout(() => finish(new Error(`monitor startup timed out; see ${log}`)), 45000);
    child.on("error", (error) => finish(error));
    child.on("message", (status) => {
      if (matches(status)) finish(null, status);
    });
    child.on("exit", async () => {
      // A simultaneous activation may have won the pipe lock. Accept only an
      // armed bridge for exactly the requested thread and endpoint.
      try {
        const status = await query(pipe);
        if (matches(status)) { finish(null, status); return; }
      } catch (_) { /* diagnostic below */ }
      finish(new Error(`monitor did not start; see ${log}`));
    });
  });
}

module.exports = { address, query, listen, activate };

if (require.main === module) {
  (async () => {
    const [action, project, team, name, thread] = process.argv.slice(2);
    if (!["status", "repair", "stop", "delivery-accept", "delivery-retry"].includes(action) || !project || !team || !name || !thread) {
      throw new Error("Usage: monitor-control.js status|repair|stop|delivery-accept|delivery-retry <project> <team> <name> <thread>");
    }
    if ([team, name].some((value) => /[/\\\x00-\x1f]/.test(value) || value === "." || value === "..")) {
      throw new Error("invalid team or agent name");
    }
    const root = path.resolve(__dirname, "../../../..");
    const pipe = address(root, project, [{ team, name }]);
    if (action.startsWith("delivery-")) {
      const recovery = await listen(pipe, () => ({ready:false}), () => Promise.reject(new Error("delivery recovery in progress")));
      try {
        const file = path.join(root, "run", `codex-bridge.${team}.${name}.delivery.json`);
        const receipt = JSON.parse(fs.readFileSync(file, "utf8"));
        if (receipt.version !== 1 || receipt.thread !== thread ||
            JSON.stringify(receipt.identities) !== JSON.stringify([{team, name}]) ||
            canonicalPath(receipt.project) !== canonicalPath(project)) {
          throw new Error("delivery receipt does not match the requested session");
        }
        if (action === "delivery-retry") {
          if (receipt.phase === "accepted") throw new Error("accepted delivery cannot be retried; use delivery-accept");
          fs.unlinkSync(file);
        } else {
          receipt.phase = "accepted";
          const tmp = `${file}.${process.pid}.tmp`;
          try { fs.writeFileSync(tmp, JSON.stringify(receipt), {flag:"wx", mode:0o600}); fs.renameSync(tmp, file); }
          finally { if (fs.existsSync(tmp)) fs.unlinkSync(tmp); }
        }
        console.log("Delivery decision recorded; run agmsg mode monitor in the owning session.");
      } finally { await new Promise((resolve) => recovery.close(resolve)); }
      return;
    }
    let status;
    // A legacy bridge may still be blocked in a slow synchronous Bash check.
    // Let it answer before validating the exact thread and asking it to stop.
    try { status = await query(pipe, "status", action === "stop" ? 45000 : 1500); } catch (error) {
      if (action === "stop" && ["ENOENT", "ECONNREFUSED"].includes(error.code)) return;
      throw error;
    }
    if ((action !== "stop" && !status.ready) || status.thread !== thread || !Number.isInteger(status.pid) || status.pid <= 0) {
      throw new Error("no armed monitor for the requested thread");
    }
    if (action === "stop") {
      await query(pipe, "stop");
      return;
    }
    if (action === "repair") {
      // Migration only: fix a legacy launcher's MSYS PID after the live pipe
      // and the bridge's own metadata independently agree on its identity.
      const base = path.join(root, "run", `codex-bridge.${team}.${name}`);
      const meta = Object.fromEntries(fs.readFileSync(`${base}.meta`, "utf8").trim().split(/\r?\n/)
        .map((line) => { const i = line.indexOf("="); return [line.slice(0, i), line.slice(i + 1)]; }));
      if (meta.pid !== String(status.pid) || meta.type !== "codex" || meta.identities !== `${team}/${name}` ||
          canonicalPath(meta.project) !== canonicalPath(project)) {
        throw new Error("bridge metadata does not match the live monitor; leaving its PID unchanged");
      }
      const temp = `${base}.pid.${process.pid}.tmp`;
      try {
        fs.writeFileSync(temp, `${status.pid}\n`, { flag: "wx" });
        fs.renameSync(temp, `${base}.pid`);
      } finally { if (fs.existsSync(temp)) fs.unlinkSync(temp); }
    }
    console.log(status.pid);
  })().catch((error) => { console.error(error.message); process.exitCode = 1; });
}
