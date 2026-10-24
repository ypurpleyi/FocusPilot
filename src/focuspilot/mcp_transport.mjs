// Bounded MCP JSONL over one approved local pipe. No TCP or child processes.
import net from 'node:net';

export class JsonFrames {
  constructor(limit=262144) { this.limit=limit; this.buffer=Buffer.alloc(0); }
  feed(chunk) {
    this.buffer=Buffer.concat([this.buffer,chunk]); const records=[]; let end;
    while((end=this.buffer.indexOf(10))>=0) {
      if(end>this.limit) throw Error('frame_too_large');
      const text=new TextDecoder('utf-8',{fatal:true}).decode(this.buffer.subarray(0,end));
      this.buffer=this.buffer.subarray(end+1);
      // Inspect JSON punctuation before parse: reject duplicate keys and deep
      // nesting instead of silently accepting last-key-wins JSON semantics.
      const stack=[];
      for(const match of text.matchAll(/"(?:[^"\\]|\\.)*"|[{}\[\],:]/g)) {
        const token=match[0], top=stack.at(-1);
        if(token==='{' || token==='[') {
          stack.push({object:token==='{',keys:new Set(),key:true});
          if(stack.length>64) throw Error('json_too_deep');
        } else if(token==='}' || token===']') stack.pop();
        else if(token===',' && top?.object) top.key=true;
        else if(token[0]==='"' && top?.object && top.key) {
          const key=JSON.parse(token); if(top.keys.has(key)) throw Error('duplicate_json_key');
          top.keys.add(key); top.key=false;
        }
      }
      const record=JSON.parse(text);
      if(!record || Array.isArray(record) || typeof record!=='object') throw Error('object_required');
      records.push(record);
    }
    if(this.buffer.length>this.limit) throw Error('frame_too_large');
    return records;
  }
  finish() { if(this.buffer.length) throw Error('truncated_frame'); }
}

export class PipeTransport {
  constructor(path,{connect=options=>net.createConnection(options), timeoutMs=60000}={}) {
    if(!/^\\\\\.\\pipe\\LOCAL\\FocusPilot\.[a-f0-9]{32}$/.test(path)) throw Error('invalid_pipe');
    this.path=path; this.connect=connect; this.timeoutMs=Math.min(timeoutMs,60000);
    this.closed=false; this.connected=false; this.frames=new JsonFrames(); this.pending=new Set();
    this.seen=new Set(); this.total=0; this.queued=0; this.queuedBytes=0; this.tail=Promise.resolve();
    this.writes=new Set();
    this.listeners={message:new Set(),error:new Set(),close:new Set()};
  }
  onMessage(fn) {return this.on('message',fn);}
  onError(fn) {return this.on('error',fn);}
  onClose(fn) {return this.on('close',fn);}
  on(kind,fn) {this.listeners[kind].add(fn);return ()=>this.listeners[kind].delete(fn);}
  emit(kind,value) {for(const fn of [...this.listeners[kind]]) fn(value);}
  start() {
    if(this.closed) return Promise.reject(Error('transport_closed'));
    if(this.opening) return this.opening;
    this.opening=new Promise((resolve,reject)=>{
      this.rejectOpening=reject;
      this.timer=setTimeout(()=>this.fail(Error('absolute_deadline')),this.timeoutMs);
      try {
        this.socket=this.connect({path:this.path});
        this.socket.on('connect',()=>{
          if(this.closed) return;
          this.connected=true; this.rejectOpening=undefined; resolve();
        });
        this.socket.on('data',chunk=>{
          if(this.closed) return;
          try {
            this.total+=chunk.length; if(this.total>4194304) throw Error('total_output_limit');
            for(const record of this.frames.feed(chunk)) {
              if(this.closed) break;
              if(record.jsonrpc!=='2.0' || !['string','number'].includes(typeof record.id)
                  || !this.pending.delete(record.id) || 'method' in record
                  || ('result' in record)===('error' in record)
                  || Object.keys(record).some(key=>!['jsonrpc','id','result','error'].includes(key)))
                throw Error('unexpected_response');
              this.emit('message',record);
            }
          } catch(error) {this.fail(error);}
        });
        this.socket.on('end',()=>{
          if(this.closed) return;
          try {this.frames.finish();} catch(error) {this.fail(error);return;}
          this.fail(Error('peer_disconnected'));
        });
        this.socket.on('error',error=>this.fail(Error('pipe_io_failed:'+
          (['EACCES','EPERM','ENOENT','ECONNRESET','EPIPE','EBUSY'].includes(error.code)?error.code:'other'))));
        this.socket.on('close',()=>{if(!this.closed) this.fail(Error('pipe_closed'));});
      } catch(error) {this.fail(error);}
    });
    return this.opening;
  }
  async send(record,{signal}={}) {
    try {
      if(this.closed || !this.connected || signal?.aborted) throw Error('transport_closed_or_cancelled');
      if(record.jsonrpc!=='2.0' || typeof record.method!=='string') throw Error('invalid_request');
      const request='id' in record;
      if(request && (!['string','number'].includes(typeof record.id) || this.seen.has(record.id)))
        throw Error('invalid_request_id');
      if(!['initialize','notifications/initialized','tools/list','tools/call','ping'].includes(record.method))
        throw Error('method_not_allowed');
      const data=Buffer.from(JSON.stringify(record)+'\n');
      if(data.length>65537 || ++this.queued>32 || (this.queuedBytes+=data.length)>262144)
        throw Error('send_limit');
      if(request) this.seen.add(record.id);
      const abort=()=>this.fail(Error('cancelled'));
      signal?.addEventListener('abort',abort,{once:true});
      const task=this.tail.then(()=>new Promise((resolve,reject)=>{
        if(this.closed || signal?.aborted) {reject(Error('cancelled'));return;}
        if(request) this.pending.add(record.id);
        this.writes.add(reject);
        try {
          this.socket.write(data,error=>{
            this.writes.delete(reject);
            error?reject(Error('pipe_write_failed')):resolve();
          });
        } catch(error) {this.writes.delete(reject);reject(error);}
      }));
      this.tail=task.catch(()=>{});
      try {await task;} finally {
        this.queued--;this.queuedBytes-=data.length;
        signal?.removeEventListener('abort',abort);
      }
    } catch(error) {this.fail(error);throw error;}
  }
  fail(error) {
    if(this.closed) return;
    this.failureCode=/^[A-Za-z_:-]{1,80}$/.test(error?.message)?error.message:'transport_failed';
    try {this.emit('error',error);} finally {void this.close();}
  }
  async close() {
    if(this.closed) return;
    this.closed=true;this.connected=false;clearTimeout(this.timer);
    this.pending.clear();this.frames.buffer=Buffer.alloc(0);
    for(const reject of this.writes) reject(Error('transport_closed'));
    this.writes.clear();
    this.rejectOpening?.(Error('transport_closed')); this.rejectOpening=undefined;
    this.socket?.destroy();this.emit('close');
  }
}
