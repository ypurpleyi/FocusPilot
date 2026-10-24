import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {PipeTransport, JsonFrames} from '../../src/focuspilot/mcp_transport.mjs';
import {TaskAdapter, verifyTools} from '../../src/focuspilot/mcp_adapter.mjs';
import {validateModelRequest, makeModelFetch} from '../../src/focuspilot/model_fetch.mjs';

const pipe = '\\\\.\\pipe\\LOCAL\\FocusPilot.' + 'a'.repeat(32);
const contract = {
  list_tasks: {inputSchema:{type:'object'},outputSchema:{type:'object'}},
  find_duplicate_tasks: {inputSchema:{type:'object'},outputSchema:{type:'object'}}
};
const tools = Object.entries(contract).map(([name, schema]) => ({name, ...schema}));
const value = {tasks:[{id:'x',title:'fixture',due_at:null,importance:2,estimated_minutes:30}],truncated:false};
const result = () => ({content:[{type:'text',text:JSON.stringify(value)}],structuredContent:structuredClone(value)});
class Socket extends EventEmitter {
  writes=[];
  write(bytes, callback) { this.writes.push(bytes); callback(); return true; }
  destroy() { this.destroyed=true; this.emit('close'); }
}
async function connected() {
  const socket=new Socket();
  const transport=new PipeTransport(pipe,{connect:()=>socket});
  const opening=transport.start(); socket.emit('connect'); await opening;
  return {socket,transport};
}
const req = id => ({jsonrpc:'2.0',id,method:'tools/list'});
const reply = id => Buffer.from(JSON.stringify({jsonrpc:'2.0',id,result:{}})+'\n');

test('fragmented UTF8, CRLF, and malformed frames', () => {
  const data=Buffer.from('{"x":"任务"}\r\n');
  const frames=new JsonFrames(100);
  assert.deepEqual(frames.feed(data.subarray(0,8)),[]);
  assert.deepEqual(frames.feed(data.subarray(8)),[{x:'任务'}]);
  for(const data of [Buffer.from('x'.repeat(101)),Buffer.from('{"id":1,"id":2}\n'),Buffer.from('[]\n'),Buffer.from('{"x":NaN}\n'),Buffer.from([255,10]),Buffer.from('{"x":'+ '['.repeat(66)+'0'+']'.repeat(66)+'}\n')])
    assert.throws(()=>new JsonFrames(100).feed(data));
  frames.feed(Buffer.from('{')); assert.throws(()=>frames.finish());
});
test('exact request association and duplicate response failure', async () => {
  const {socket,transport}=await connected(); let received=0,closed=0;
  transport.onMessage(()=>received++); transport.onClose(()=>closed++);
  await transport.send(req(1)); socket.emit('data',reply(1));
  assert.equal(received,1); socket.emit('data',reply(1));
  assert.equal(closed,1); assert.equal(received,1);
  await transport.close(); assert.equal(closed,1);
});
test('unknown response, EOF fragment, and oversized send revoke transport', async () => {
  for(const mode of ['unknown','fragment','large']) {
    const {socket,transport}=await connected();
    if(mode==='unknown') socket.emit('data',reply(99));
    if(mode==='fragment') {socket.emit('data',Buffer.from('{'));socket.emit('end');}
    if(mode==='large') await assert.rejects(transport.send({...req(1),params:{x:'x'.repeat(65536)}}));
    assert.equal(transport.closed,true);
    await assert.rejects(transport.send(req(2)));
  }
});
test('cancelled queued sends never dispatch, and close is idempotent', async () => {
  const {socket,transport}=await connected();
  const controller=new AbortController();
  const sent=transport.send(req(1),{signal:controller.signal});
  controller.abort(); await assert.rejects(sent);
  assert.equal(socket.writes.length,0); assert.equal(transport.closed,true);
  await transport.close(); await transport.close();
});
test('disconnect closes pending run and rejects further dispatch', async () => {
  const {socket,transport}=await connected(); await transport.send(req(1));
  socket.emit('error',new Error('fixed IO error'));
  await assert.rejects(transport.send(req(2))); assert.equal(transport.closed,true);
});
test('closing settles a write even when the IO callback never arrives', async () => {
  const {socket,transport}=await connected();
  socket.write=()=>true;
  const pending=transport.send(req(1));
  await Promise.resolve();
  await transport.close();
  const outcome=await Promise.race([pending.then(()=>false,()=>true),new Promise(resolve=>setTimeout(()=>resolve(false),30))]);
  assert.equal(outcome,true);
});
test('absolute deadline closes connection and arbitrary pipe names fail', async () => {
  assert.throws(()=>new PipeTransport('http://localhost/mcp'));
  const socket=new Socket(); const transport=new PipeTransport(pipe,{connect:()=>socket,timeoutMs:15});
  const closed=new Promise(resolve=>transport.onClose(resolve));
  const opening=transport.start();socket.emit('connect');await opening;await closed;
  assert.equal(transport.closed,true);
});
test('contract rejects extra tool and schema mismatch', () => {
  verifyTools(tools,contract);
  assert.throws(()=>verifyTools([...tools,{name:'write'}],contract));
  const changed=structuredClone(tools); changed[0].inputSchema.additionalProperties=true;
  assert.throws(()=>verifyTools(changed,contract));
});
function adapter(overrides={}) {
  const client={connect:async()=>({serverInfo:{name:'focuspilot-readonly'}}),listTools:async()=>tools,
    callTool:async()=>result(),close:async()=>{},...overrides};
  return new TaskAdapter(client,{close:async()=>{}},contract);
}
test('adapter validates and returns structured result', async () => {
  const gateway=adapter(); await gateway.initialize();
  const answer=await gateway.call('list_tasks',{limit:100});
  assert.deepEqual(answer.structuredContent,value); assert.deepEqual(answer.details,value);
  await gateway.close(); await assert.rejects(gateway.call('list_tasks',{}));
});
test('forbidden arguments never reach client and invalidate run', async () => {
  for(const [name,args] of [['delete_task',{}],['list_tasks',{sql:'DELETE'}],['list_tasks',{limit:true}],['find_duplicate_tasks',{titles:[]} ]]) {
    let calls=0; const gateway=adapter({callTool:async()=>{calls++;return result();}}); await gateway.initialize();
    await assert.rejects(gateway.call(name,args)); assert.equal(calls,0); assert.equal(gateway.ready,false);
  }
});
test('tool failure and inconsistent results never become empty success', async () => {
  for(const bad of [{isError:true,content:[]},{...result(),structuredContent:{tasks:[],truncated:false}},
      {...result(),structuredContent:{tasks:[{...value.tasks[0],importance:99}],truncated:false}}]) {
    const gateway=adapter({callTool:async()=>bad}); await gateway.initialize();
    await assert.rejects(gateway.call('list_tasks',{})); assert.equal(gateway.ready,false);
  }
});
test('cancel invalidates late result even if upstream ignores AbortSignal', async () => {
  let finish; const gateway=adapter({callTool:()=>new Promise(resolve=>finish=resolve)}); await gateway.initialize();
  const controller=new AbortController();
  const pending=gateway.call('list_tasks',{},controller.signal);
  controller.abort(); finish(result()); await assert.rejects(pending);
  assert.equal(gateway.ready,false);
});
test('discovery mismatch fails closed before tools usable', async () => {
  const gateway=adapter({listTools:async()=>[]}); await assert.rejects(gateway.initialize());
  await assert.rejects(gateway.call('list_tasks',{}));
});
test('ninth tool attempt fails and does not dispatch', async () => {
  let calls=0; const gateway=adapter({callTool:async()=>{calls++;return result();}}); await gateway.initialize();
  for(let i=0;i<8;i++) await gateway.call('list_tasks',{});
  await assert.rejects(gateway.call('list_tasks',{})); assert.equal(calls,8);
});
test('model requests are restricted to fixed origin, model and token budget', async () => {
  const init={method:'POST',body:JSON.stringify({model:'deepseek-flash',max_tokens:2048,messages:[]})};
  assert.equal(validateModelRequest('https://api.deepseek.com/chat/completions',init).hostname,'api.deepseek.com');
  for(const url of ['https://evil.example/chat/completions','http://api.deepseek.com/chat/completions',
      'https://api.deepseek.com:8443/chat/completions','https://api.deepseek.com/files',
      'https://user:secret@api.deepseek.com/chat/completions','https://api.deepseek.com/chat/completions?url=evil'])
    assert.throws(()=>validateModelRequest(url,init));
  for(const body of [{model:'pro',max_tokens:100},{model:'deepseek-flash',max_tokens:999999},
      {model:'deepseek-flash'}, {model:'deepseek-flash',max_tokens:true}])
    assert.throws(()=>validateModelRequest('https://api.deepseek.com/chat/completions',{...init,body:JSON.stringify(body)}));
  const controller=new AbortController();controller.abort();
  await assert.rejects(makeModelFetch(pipe)('https://api.deepseek.com/chat/completions',{...init,signal:controller.signal}));
});
