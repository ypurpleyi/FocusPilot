// Product adapter: only fixed read-only tools; all network uses the host relay.
import fs from 'node:fs';
import path from 'node:path';
import {McpClient} from '@earendil-works/pi-mcp';
import {PipeTransport} from './mcp_transport.mjs';
import {TaskAdapter, TOOL_NAMES, piName} from './mcp_adapter.mjs';
import {makeModelFetch} from './model_fetch.mjs';

export default function focuspilotTasks(pi) {
  const config=JSON.parse(fs.readFileSync(new URL('./focuspilot_app_config.json',import.meta.url),'utf8'));
  const contract=JSON.parse(fs.readFileSync(new URL('./focuspilot_mcp_contract.json',import.meta.url),'utf8'));
  const networkFetch=makeModelFetch(config.model_pipe);
  globalThis.fetch=async(input,init)=>{
    try {return await networkFetch(input,init);}
    catch(error) {
      fs.writeFileSync(path.join(process.cwd(),'focuspilot-network-diagnostic.json'),JSON.stringify({
        cause:/^[a-zA-Z_:-]{1,100}$/.test(error?.message)?error.message:'model_request_failed'}));
      throw error;
    }
  };
  const transport=new PipeTransport(config.query_pipe);
  const client=new McpClient({name:'focuspilot-app',version:'0.1.0',capabilities:{},requestTimeoutMs:10000});
  const adapter=new TaskAdapter(client,transport,contract);
  for(const name of TOOL_NAMES) pi.registerTool({
    name:piName(name),label:name,
    description:name==='list_tasks'?'List current pending tasks. This tool cannot write data.':
      'Find existing pending tasks with exactly the supplied titles. Candidate matches only; user decides.',
    parameters:contract[name].inputSchema,outputSchema:contract[name].outputSchema,
    defaultActive:false,exposure:'direct',executionMode:'sequential',
    execute:async(_id,args,signal)=>adapter.call(name,args,signal),
  });
  const ready=path.join(process.cwd(),'focuspilot-ready.json');
  pi.on('session_start',async()=>{
    pi.setActiveTools([]);
    try {
      await adapter.initialize();pi.setActiveTools(TOOL_NAMES.map(piName));
      const active=pi.getActiveTools().slice().sort();
      if(JSON.stringify(active)!==JSON.stringify(TOOL_NAMES.map(piName)))throw Error('tool_set_mismatch');
      fs.writeFileSync(ready,JSON.stringify({ready:true,tools:active}));
    } catch {
      pi.setActiveTools([]);await adapter.close();fs.writeFileSync(ready,JSON.stringify({ready:false}));
      throw Error('FocusPilot tool initialization failed');
    }
  });
  pi.on('session_shutdown',async()=>{pi.setActiveTools([]);await adapter.close();});
}
