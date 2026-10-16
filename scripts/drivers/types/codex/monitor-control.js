"use strict";

// Windows named pipes provide an atomic, process-lifetime lock. No PID selected
// from a writable file is used to stop a process.
const net = require("net");
const crypto = require("crypto");
const fs = require("fs");
const { spawn } = require("child_process");

function address(root, project, pair) {
  const key = crypto.createHash("sha256")
    .update(JSON.stringify([fs.realpathSync(root).toLowerCase(), fs.realpathSync(project).toLowerCase(), pair])).digest("hex");
  return `\\\\.\\pipe\\agmsg-codex-${key}`;
}

function query(pipe, action = "status") {
  return new Promise((resolve, reject) => {
    const socket = net.connect(pipe);
    let data = "";
    socket.setTimeout(action === "stop" ? 45000 : 1500, () => socket.destroy(new Error("monitor control timeout")));
    socket.on("connect", () => socket.end(`${action}\n`));
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

async function listen(pipe, status, stop) {
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
        Promise.resolve(stop()).then(() => socket.end(JSON.stringify(result)), () => socket.destroy());
      } else socket.end(JSON.stringify(result));
    });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
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
