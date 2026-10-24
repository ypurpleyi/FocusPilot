// Offline prototype: only explicitly staged configuration and two fixed tools.
import fs from 'node:fs';
import path from 'node:path';
import {McpClient} from '@earendil-works/pi-mcp';
import {PipeTransport} from './mcp_transport.mjs';
import {TaskAdapter, TOOL_NAMES, piName} from './mcp_adapter.mjs';

export default function focuspilotMcp(pi) {
  const config=JSON.parse(fs.readFileSync(new URL('./focuspilot_mcp_config.json',import.meta.url),'utf8'));
  const contract=JSON.parse(fs.readFileSync(new URL('./focuspilot_mcp_contract.json',import.meta.url),'utf8'));
  if(config.offline_probe!==true || !['normal','cancel','timeout'].includes(config.scenario)) throw Error('offline_configuration_required');
  const transport=new PipeTransport(config.pipe);
  const client=new McpClient({name:'focuspilot-adapter',version:'0.1.0',capabilities:{},requestTimeoutMs:10000});
  const adapter=new TaskAdapter(client,transport,contract);
  const report={model_called:false,fixed_driver:true,ready:false,shutdown_called:false,
    pipe_tag:config.pipe.slice(-32),scenario:config.scenario};
  const evidence=path.join(process.cwd(),'focuspilot-adapter-evidence.json');
  const save=()=>fs.writeFileSync(evidence,JSON.stringify(report),{encoding:'utf8',mode:0o600});
  const definitions=TOOL_NAMES.map(name=>({
    name:piName(name),label:name,description:name==='list_tasks'?'List pending tasks only.':'Find pending same-title duplicate candidates. User decides.',
    parameters:contract[name].inputSchema,outputSchema:contract[name].outputSchema,
    exposure:'direct',defaultActive:false,executionMode:'sequential',
    execute:async(_id,args,signal)=>adapter.call(name,args,signal),
  }));
  for(const definition of definitions) pi.registerTool(definition);
  pi.on('session_start',async()=>{
    pi.setActiveTools([]);
    try {
      await adapter.initialize();
      pi.setActiveTools(definitions.map(tool=>tool.name));
      report.active_tools_at_ready=pi.getActiveTools().slice().sort();
      report.registered_tools=pi.getAllTools().map(tool=>tool.name).sort();
      report.ready=true;
      save();
      if(config.scenario==='normal') {
        // Fixed offline driver calls exactly the callbacks registered above.
        // This demonstrates binding, not autonomous model tool selection.
        const pending=await definitions.find(tool=>tool.name===piName('list_tasks')).execute('offline-list',{limit:100});
        const duplicates=await definitions.find(tool=>tool.name===piName('find_duplicate_tasks')).execute(
          'offline-duplicates',{titles:['fictional-report']});
        report.pending=pending.structuredContent;
        report.duplicates=duplicates.structuredContent;
      } else {
        // Wait for the host's deterministic cancellation/deadline injection.
        await new Promise(resolve=>{client.onClose(resolve);if(client.connectionState==='closed')resolve();});
        let rejected=false;
        try {await definitions[0].execute('after-stop',{titles:['fictional-report']});} catch {rejected=true;}
        report.call_after_stop_rejected=rejected;
        report.ready=false;
        pi.setActiveTools([]);
        report.active_tools_after_stop=pi.getActiveTools();
      }
      save();
    } catch(error) {
      report.ready=false;report.failure='adapter_initialization_or_query_failed';
      report.cause=transport.failureCode || (/^[A-Za-z_:-]{1,80}$/.test(error?.message)?error.message:'client_failed');
      pi.setActiveTools([]);await adapter.close();save();
      throw Error('FocusPilot offline adapter failed');
    }
  });
  pi.on('session_shutdown',async()=>{
    await adapter.close();pi.setActiveTools([]);
    report.active_tools_after_shutdown=pi.getActiveTools();
    report.shutdown_called=true;save();
  });
}
