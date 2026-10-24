'use strict';
// Fixed offline client, no model, user code, paths or arbitrary tool selection.
const net = require('node:net');
const socket = net.createConnection(process.argv[2]);
const mode = process.argv[3] || 'normal';
const timer = setTimeout(() => process.exit(10), 15000);
let buffer = Buffer.alloc(0), phase = 0;
const report = {};
function send(id, method, params) {
  socket.write(JSON.stringify({jsonrpc:'2.0', id, method, ...(params ? {params} : {})})+'\n');
}
socket.on('connect', () => send(1, 'initialize', {
  protocolVersion:'2025-11-25', capabilities:{}, clientInfo:{name:'focuspilot-fixed',version:'1'}
}));
socket.on('data', data => {
  buffer = Buffer.concat([buffer, data]);
  if (buffer.length > 262144) process.exit(11);
  let end;
  while ((end = buffer.indexOf(10)) >= 0) {
    const record = JSON.parse(buffer.subarray(0,end).toString('utf8'));
    buffer = buffer.subarray(end+1);
    if (record.error || !record.result || record.result.isError) process.exit(12);
    if (record.id !== ++phase) process.exit(13);
    if (phase === 1) {
      socket.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n');
      if (mode === 'illegal') send(2, 'tools/call', {name:'list_tasks', arguments:{sql:'DELETE FROM tasks'}});
      else if (mode === 'linger') { report.initialized = true; console.log(JSON.stringify(report)); }
      else send(2, 'tools/list');
    } else if (phase === 2) {
      report.tools = record.result.tools.map(tool => tool.name).sort();
      send(3, 'tools/call', {name:'list_tasks',arguments:{limit:100}});
    } else if (phase === 3) {
      report.pending = record.result.structuredContent;
      send(4, 'tools/call', {name:'find_duplicate_tasks',arguments:{titles:['fictional-report']}});
    } else if (phase === 4) {
      report.duplicates = record.result.structuredContent;
      console.log(JSON.stringify(report)); clearTimeout(timer); socket.end();
    }
  }
});
socket.on('error', error => { console.error(['EACCES','EPERM','ENOENT','ECONNRESET'].includes(error.code) ? error.code : 'PIPE_CONNECT_FAILED'); process.exit(14); });
socket.on('close', () => { clearTimeout(timer); process.exit(phase === 4 ? 0 : 15); });
