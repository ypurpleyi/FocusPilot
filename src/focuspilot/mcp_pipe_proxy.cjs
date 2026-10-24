'use strict';
// Transport only. The trusted host performs MCP authorization and queries.
const net = require('node:net');
const pipe = process.argv[2];
if (!pipe || !/^\\\\\.\\pipe\\LOCAL\\FocusPilot\.[a-f0-9]{32}$/.test(pipe)) process.exit(2);
const socket = net.createConnection(pipe);
let input = Buffer.alloc(0), output = Buffer.alloc(0), bytes = 0;
const timer = setTimeout(() => process.exit(3), 60000);
function frames(chunk, pending, limit, send) {
  bytes += chunk.length;
  if (bytes > 2097152) throw Error('total limit');
  pending = Buffer.concat([pending, chunk]);
  let end;
  while ((end = pending.indexOf(10)) >= 0) {
    if (end > limit) throw Error('frame limit');
    send(pending.subarray(0, end + 1));
    pending = pending.subarray(end + 1);
  }
  if (pending.length > limit) throw Error('frame limit');
  return pending;
}
socket.on('connect', () => {
  process.stdin.on('data', chunk => {
    try { input = frames(chunk, input, 65536, line => {
      if (!socket.write(line)) process.stdin.pause();
    }); } catch { process.exit(4); }
  });
  socket.on('drain', () => process.stdin.resume());
  process.stdin.on('end', () => { if (input.length) process.exit(4); socket.end(); });
  process.stdin.resume();
});
socket.on('data', chunk => {
  try { output = frames(chunk, output, 262144, line => {
    if (!process.stdout.write(line)) socket.pause();
  }); } catch { process.exit(4); }
});
process.stdout.on('drain', () => socket.resume());
socket.on('error', () => process.exit(5));
socket.on('end', () => { clearTimeout(timer); process.exit(output.length ? 4 : 0); });
process.stdout.on('error', () => process.exit(5));
process.stdin.on('error', () => process.exit(5));
