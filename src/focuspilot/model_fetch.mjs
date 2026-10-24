// Fixed-origin HTTPS through the host's encrypted byte relay. No direct fetch fallback.
import https from 'node:https';
import tls from 'node:tls';
import net from 'node:net';
import {Readable} from 'node:stream';
import zlib from 'node:zlib';

export function validateModelRequest(input,init={}) {
  const url=new URL(typeof input==='string'||input instanceof URL?input:input.url);
  if(url.protocol!=='https:'||url.hostname!=='api.deepseek.com'||url.username||url.password
      ||url.port && url.port!=='443'||url.search||url.hash
      ||!['/chat/completions','/v1/chat/completions'].includes(url.pathname)) throw Error('model_origin_rejected');
  const method=init.method || (input instanceof Request?input.method:'GET');
  if(method!=='POST'||typeof init.body!=='string'||Buffer.byteLength(init.body)>262144) throw Error('model_request_rejected');
  const body=JSON.parse(init.body);
  const limit=body.max_tokens??body.max_completion_tokens;
  if(body.model!=='deepseek-flash'||!Number.isInteger(limit)||limit<1||limit>4096) throw Error('model_budget_rejected');
  return url;
}

export function makeModelFetch(pipe) {
  if(!/^\\\\\.\\pipe\\LOCAL\\FocusPilot\.[a-f0-9]{32}$/.test(pipe)) throw Error('invalid_model_pipe');
  let attempts=0;
  return async(input,init={})=>{
    if(input instanceof Request) {
      init={method:input.method,headers:input.headers,signal:input.signal,...init};
      if(init.body===undefined) init.body=await input.clone().text();
    }
    const url=validateModelRequest(input,init);
    if(++attempts>4||init.signal?.aborted) throw Error('model_cancelled_or_budget_exceeded');
    return new Promise((resolve,reject)=>{
      const agent=new https.Agent({keepAlive:false,maxSockets:1});
      agent.createConnection=()=>tls.connect({socket:net.createConnection({path:pipe}),
        servername:'api.deepseek.com',rejectUnauthorized:true,ALPNProtocols:['http/1.1']});
      const headers=new Headers(init.headers);headers.set('accept-encoding','identity');
      headers.set('content-length',String(Buffer.byteLength(init.body)));
      const request=https.request(url,{method:'POST',headers:Object.fromEntries(headers),agent},response=>{
        if(response.statusCode>=300&&response.statusCode<400) {
          request.destroy();agent.destroy();reject(Error('model_redirect_rejected'));return;
        }
        let bytes=0;
        response.on('data',chunk=>{bytes+=chunk.length;if(bytes>4194304)request.destroy(Error('model_output_limit'));});
        const encoding=response.headers['content-encoding'];
        let stream=response;
        if(encoding==='gzip')stream=response.pipe(zlib.createGunzip());
        else if(encoding==='deflate')stream=response.pipe(zlib.createInflate());
        else if(encoding==='br')stream=response.pipe(zlib.createBrotliDecompress());
        else if(encoding&&encoding!=='identity'){request.destroy();reject(Error('model_encoding_rejected'));return;}
        const resultHeaders=new Headers();
        for(const [key,value] of Object.entries(response.headers)) {
          if(value!==undefined&&!['content-encoding','content-length','transfer-encoding'].includes(key))
            resultHeaders.set(key,Array.isArray(value)?value.join(','):value);
        }
        response.on('end',()=>agent.destroy());
        response.on('close',()=>init.signal?.removeEventListener('abort',abort));
        resolve(new Response(Readable.toWeb(stream),{status:response.statusCode,headers:resultHeaders}));
      });
      const abort=()=>{request.destroy();agent.destroy();reject(Error('model_cancelled'));};
      init.signal?.addEventListener('abort',abort,{once:true});
      request.setTimeout(60000,abort);
      request.on('error',()=>{agent.destroy();init.signal?.removeEventListener('abort',abort);reject(Error('model_connection_failed'));});
      request.end(init.body);
    });
  };
}
