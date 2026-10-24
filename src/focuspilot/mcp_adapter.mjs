// Fixed business adapter. MCP discovery cannot grant new tools or schemas.
export const TOOL_NAMES=['find_duplicate_tasks','list_tasks'];
export const piName=name=>'mcp__focuspilot__'+name;
function canonical(value) {
  if(Array.isArray(value)) return '['+value.map(canonical).join(',')+']';
  if(value && typeof value==='object') return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}';
  return JSON.stringify(value);
}
export function verifyTools(tools,contract) {
  if(!Array.isArray(tools) || tools.length!==2
      || canonical(tools.map(tool=>tool.name).sort())!==canonical(TOOL_NAMES)) throw Error('unexpected_tools');
  for(const tool of tools) {
    if(canonical(tool.inputSchema)!==canonical(contract[tool.name]?.inputSchema)
        || canonical(tool.outputSchema)!==canonical(contract[tool.name]?.outputSchema)) throw Error('schema_mismatch');
  }
}
function object(value) {return value && typeof value==='object' && !Array.isArray(value);}
function keys(value,allowed) {return object(value) && Object.keys(value).every(key=>allowed.includes(key));}
function argumentsValid(name,args) {
  if(name==='list_tasks') return keys(args,['limit']) && (!('limit' in args) || Number.isInteger(args.limit)&&args.limit>=1&&args.limit<=100);
  return name==='find_duplicate_tasks' && keys(args,['titles']) && Array.isArray(args.titles)
    && args.titles.length>=1 && args.titles.length<=100
    && args.titles.every(title=>typeof title==='string' && title.length<=1000 && /\S/.test(title));
}
function validateResult(answer) {
  const value=answer?.structuredContent;
  if(answer?.isError || !keys(value,['tasks','truncated']) || Object.keys(value).length!==2
      || !Array.isArray(value.tasks) || value.tasks.length>100 || typeof value.truncated!=='boolean') throw Error('invalid_result');
  const fields=['id','title','due_at','importance','estimated_minutes'];
  for(const task of value.tasks) {
    if(!keys(task,fields)||Object.keys(task).length!==5||typeof task.id!=='string'||!task.id
      ||typeof task.title!=='string'||!task.title || !(task.due_at===null||typeof task.due_at==='string')
      ||!Number.isInteger(task.importance)||task.importance<1||task.importance>3
      ||!Number.isInteger(task.estimated_minutes)||task.estimated_minutes<1||task.estimated_minutes>1440) throw Error('invalid_task_result');
  }
  const text=JSON.stringify(value);
  if(Buffer.byteLength(text)>65536 || !Array.isArray(answer.content) || answer.content.length!==1
      || answer.content[0].type!=='text' || Buffer.byteLength(answer.content[0].text)>65536
      || canonical(JSON.parse(answer.content[0].text))!==canonical(value)) throw Error('inconsistent_result');
  return {content:[{type:'text',text}],structuredContent:value,details:value};
}
export class TaskAdapter {
  constructor(client,transport,contract) {
    this.client=client;this.transport=transport;this.contract=contract;
    this.ready=false;this.closed=false;this.attempts=0;this.generation=0;
    this.client.onClose?.(()=>{this.ready=false;this.closed=true;this.generation++;});
  }
  async initialize() {
    try {
      if(this.closed) throw Error('adapter_closed');
      const response=await this.client.connect(this.transport);
      if(response.serverInfo?.name!=='focuspilot-readonly') throw Error('wrong_server');
      verifyTools(await this.client.listTools(),this.contract);
      if(this.closed) throw Error('adapter_closed');
      this.ready=true;
    } catch(error) {await this.close();throw error;}
  }
  async call(name,args,signal) {
    const generation=this.generation;
    const abort=()=>{void this.close();};
    try {
      if(!this.ready || this.closed || signal?.aborted) throw Error('adapter_unavailable');
      if(++this.attempts>8 || !argumentsValid(name,args)) throw Error('tool_rejected');
      signal?.addEventListener('abort',abort,{once:true});
      const answer=await this.client.callTool(name,args,{signal,timeoutMs:10000});
      if(!this.ready||this.closed||generation!==this.generation||signal?.aborted) throw Error('stale_result');
      return validateResult(answer);
    } catch(error) {await this.close();throw error;}
    finally {signal?.removeEventListener('abort',abort);}
  }
  async close() {
    this.ready=false;this.generation++;
    if(this.closing) return this.closing;
    this.closed=true;
    this.closing=Promise.allSettled([Promise.resolve().then(()=>this.client.close()),
                                  Promise.resolve().then(()=>this.transport.close())]).then(()=>{});
    return this.closing;
  }
}
