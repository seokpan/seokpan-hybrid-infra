// Separate bounded HTTP HPA load. No import-time network/browser activity.
import https from 'node:https';
import {spawn} from 'node:child_process';
import {readFileSync,writeFileSync,appendFileSync,mkdirSync,existsSync} from 'node:fs';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {performance,monitorEventLoopDelay} from 'node:perf_hooks';
import {createHash} from 'node:crypto';
export const phases=[[0,120],[10,180],[50,300],[100,300],[200,300],[300,120],[100,180],[0,600]];
export const need=(ok,why)=>{if(!ok)throw Error(why);};
export const sleep=ms=>new Promise(r=>setTimeout(r,Math.max(0,ms)));
export function validate(p,now=Date.now()){
 need(p?.schema===1&&p.workload==='guest-lobby-and-ranking-reads'&&/^[a-z0-9-]{3,46}$/.test(p.run_id),'EXACT_HPA_READ_PROFILE_REQUIRED');
 const u=new URL(p.origin);need(u.protocol==='https:'&&!u.username&&!u.password&&!u.port&&!u.search&&!u.hash&&u.pathname==='/'&&u.hostname.endsWith('.openshiftapps.com')&&!u.hostname.includes('.8zrv.'),'NEW_HTTPS_ORIGIN_REQUIRED');
 need(JSON.stringify(p.phases.map(x=>[x.vus,x.seconds]))===JSON.stringify(phases)&&p.max_vus===300&&p.think_ms===1000&&p.timeout_ms===5000&&p.stop_error_fraction===0.05,'FIXED_BOUNDED_PHASE_PROFILE_REQUIRED');
 need(/^[a-f0-9]{64}$/.test(p.control_sha256)&&p.binding?.cluster_id!=='2tejeb2farjaaoef1e74asdf6amrtpu3'
   &&/^[a-z0-9]{10,64}$/.test(p.binding?.cluster_id??'')&&['namespace_uid','application_uid'].every(k=>/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(p.binding[k]??'')),'EXACT_TARGET_BINDING_REQUIRED');
 need(Array.isArray(p.guard_argv)&&p.guard_argv.length>0&&p.guard_argv.every(x=>typeof x==='string'&&x&&!x.includes('\n')),'EXISTING_GUARD_ARGV_REQUIRED');
 const start=Date.parse(p.window.not_before),end=Date.parse(p.window.not_after);
 need(Number.isFinite(start)&&Number.isFinite(end)&&now>=start&&end-now>=2400000,'CURRENT_WINDOW_SETUP_35MIN_CLEANUP_REQUIRED');return p;
}
export function summary(rows,events=[]){
 const out={};for(const r of rows){const k=r.phase+' '+r.method+' '+r.path;
  const g=(out[k]??={phase:r.phase,method:r.method,path:r.path,N:0,http_2xx:0,http_5xx:0,transport_or_unknown:0,semantic_failure:0,elapsed:[]});
  g.N++;if(r.status>=200&&r.status<300)g.http_2xx++;if(r.status>=500)g.http_5xx++;if(r.status===null)g.transport_or_unknown++;if(r.semantic_ok===false)g.semantic_failure++;g.elapsed.push(r.elapsed_ms);
 }
 for(const g of Object.values(out)){g.elapsed.sort((a,b)=>a-b);const quantile=q=>g.elapsed.length?g.elapsed[Math.max(0,Math.ceil(g.elapsed.length*q)-1)]:null;
  g.p95_ms=quantile(.95);g.p99_ms=quantile(.99);g.http_5xx_fraction=g.N?g.http_5xx/g.N:null;delete g.elapsed;}
 return {routes:out,phases:events.map(e=>{const selected=rows.filter(x=>x.phase===e.phase),N=selected.length;
  return {phase:e.phase,vus:e.vus,actual_seconds:e.actual_seconds??null,N,rps:e.actual_seconds?N/e.actual_seconds:null,
   http_5xx_fraction:N?selected.filter(x=>x.status>=500).length/N:null,no_data_is_zero_error:false};})};
}
export function request(origin,path,method='GET',headers={},agent){
 need(['/api/v1/sessions/guest','/api/v1/rooms','/api/v1/rankings?offset=0&limit=20','/api/v1/session'].includes(path),'SCOPED_API_PATH_REQUIRED');
 return new Promise(res=>{
  const start=performance.now();let done=false,timer;
  const finish=x=>{if(done)return;done=true;clearTimeout(timer);res({...x,elapsed_ms:performance.now()-start});};
  const q=https.request(new URL(path,origin),{method,agent,rejectUnauthorized:true,headers:{Origin:origin,...headers}},r=>{
   let size=0,parts=[];r.on('data',x=>{size+=x.length;if(size>1048576){q.destroy();finish({status:null});}else parts.push(x);});
   r.on('end',()=>{let body;try{body=JSON.parse(Buffer.concat(parts).toString());}catch{}finish({status:r.statusCode,body,cookies:r.headers['set-cookie']??[]});});
  });timer=setTimeout(()=>{q.destroy();finish({status:null});},5000);q.on('error',()=>finish({status:null}));q.end();
 });
}
export function guard(argv){return new Promise((res,rej)=>{
 const c=spawn(argv[0],argv.slice(1),{shell:false,windowsHide:true,stdio:['ignore','pipe','ignore']});let text='';
 const t=setTimeout(()=>{c.kill();rej(Error('CONTROL_FEED_UNAVAILABLE'));},30000);
 c.stdout.on('data',x=>{text+=x;if(text.length>262144){c.kill();}});c.on('error',()=>{clearTimeout(t);rej(Error('CONTROL_GUARD_FAILED'));});
 c.on('exit',code=>{clearTimeout(t);try{const x=JSON.parse(text.trim().split('\n').at(-1));need(code===0&&['PASS_HPA_GUARD','WAIT_HPA_METRICS'].includes(x.status),'CONTROL_GUARD_STOP');res(x);}catch{rej(Error('CONTROL_GUARD_STOP'));}});
});}
export async function run(p,out,{send=request,checkGuard=guard,wait=sleep,now=()=>performance.now(),aborted=()=>false}={}){
 validate(p);mkdirSync(out,{mode:0o700});const started=now(),rows=[],events=[],sessions=[],resources=[],allTasks=new Set();let stopped=false,reason=null,inflight=0,issueAttempts=0,issueUnknown=0;
 const agent=new https.Agent({keepAlive:true,maxSockets:300});const delays=monitorEventLoopDelay({resolution:20});delays.enable();
 const bindGuard=async(waiting=false)=>{const g=await checkGuard(p.guard_argv);need(g.config_sha256===p.control_sha256&&JSON.stringify(g.binding)===JSON.stringify(p.binding),'GUARD_TARGET_CHANGED');
  need(g.status==='PASS_HPA_GUARD'||waiting&&g.status==='WAIT_HPA_METRICS','CONTROL_GUARD_STOP');return g;};
 const bounded=()=>need(!aborted()&&!stopped&&Date.now()<Date.parse(p.window.not_after),'LOAD_ABORT_OR_WINDOW_ENDED');
 const emit=(file,v)=>appendFileSync(join(out,file),JSON.stringify(v)+'\n',{mode:0o600});
 let monitor;
 try{
  let initial=await bindGuard(true);const metricEnd=now()+120000;
  while(initial.status==='WAIT_HPA_METRICS'&&now()<metricEnd){bounded();await wait(10000);initial=await bindGuard(true);}
  need(initial.status==='PASS_HPA_GUARD','HPA_INITIAL_METRICS_NOT_READY');
  // Guest setup is separate from the 35-minute phase denominators; no Member rows or rooms are created.
  for(let i=0;i<300;i+=10){
   bounded();await bindGuard();const setup=await Promise.allSettled(Array.from({length:10},async()=>{
    issueAttempts++;const r=await send(p.origin,'/api/v1/sessions/guest','POST',{},agent);
    const cookie=r.cookies?.find(x=>x.startsWith('seokpan_session='))?.split(';')[0];
    if(cookie&&r.body?.csrf_token)sessions.push({cookie,csrf:r.body.csrf_token});else issueUnknown++;
    need(r.status===201&&cookie&&typeof r.body?.csrf_token==='string','GUEST_SETUP_FAILED_NO_AUTOMATIC_RETRY');
   }));need(setup.every(x=>x.status==='fulfilled'),'GUEST_SETUP_FAILED_NO_AUTOMATIC_RETRY');
  }
  monitor=(async()=>{while(!stopped){await bindGuard();const x={utc:new Date().toISOString(),elapsed_ms:now()-started,cpu:process.cpuUsage(),memory:process.memoryUsage(),event_loop_p99_ms:delays.percentile(99)/1e6};resources.push(x);emit('generator.jsonl',x);await wait(10000);}})();
  monitor.catch(e=>{reason=e.message;stopped=true;});
  for(let phase=0;phase<p.phases.length;phase++){
   bounded();await bindGuard();const {vus,seconds}=p.phases[phase],begin=now(),end=begin+seconds*1000;
   const event={phase,vus,seconds,started_utc:new Date().toISOString(),started_ms:begin-started};events.push(event);emit('events.jsonl',event);
   const tasks=Array.from({length:vus},(_,vu)=>(async()=>{
    let n=0;while(now()<end&&!stopped){bounded();const path=n++%2===0?'/api/v1/rooms':'/api/v1/rankings?offset=0&limit=20';const at=now();inflight++;
     let r;try{r=await send(p.origin,path,'GET',{Cookie:sessions[vu].cookie},agent);}finally{inflight--;}
     const semantic=r.status===200&&Array.isArray(path.startsWith('/api/v1/rooms')?r.body?.rooms:r.body?.items);
     const row={phase,vu,attempt:n,method:'GET',path,status:r.status,semantic_ok:semantic,elapsed_ms:r.elapsed_ms,started_ms:at-started,utc:new Date().toISOString(),retry_of:null};rows.push(row);emit('requests.jsonl',row);
     if(r.status===401||r.status===403){reason='SESSION_OR_ORIGIN_REJECTED';stopped=true;break;}
     const recent=rows.slice(-100).filter(x=>x.phase===phase);if(recent.length>=20&&recent.filter(x=>!x.semantic_ok).length/recent.length>p.stop_error_fraction){reason='RECENT_ERROR_BUDGET_EXCEEDED';stopped=true;break;}
     await wait(Math.max(0,p.think_ms-(now()-at)));
    }
   })());
   for(const t of tasks)allTasks.add(t);const results=await Promise.allSettled(tasks);for(const t of tasks)allTasks.delete(t);
   need(results.every(x=>x.status==='fulfilled'),'WORKER_FAILED');if(vus===0)while(now()<end&&!stopped){bounded();await wait(Math.min(1000,end-now()));}
   event.finished_utc=new Date().toISOString();event.actual_seconds=(now()-begin)/1000;emit('events.jsonl',{...event,event:'phase_finished'});bounded();
  }
 }catch(e){reason=reason||e.message;stopped=true;}
 finally{
  stopped=true;await Promise.allSettled([...allTasks]);if(monitor)await monitor.catch(()=>{});delays.disable();
  let cleaned=0,unknown=0;const cleanupDeadline=Date.now()+60000;
  for(let i=0;i<sessions.length;i+=10){if(Date.now()>=cleanupDeadline){unknown+=sessions.length-i;break;}
   await Promise.all(sessions.slice(i,i+10).map(async s=>{try{const r=await send(p.origin,'/api/v1/session','DELETE',{Cookie:s.cookie,'X-CSRF-Token':s.csrf},agent);if([204,401].includes(r.status))cleaned++;else unknown++;}catch{unknown++;}}));}
  agent.destroy();const receipt={status:reason?'ABORTED_HPA_LOAD':'COMPLETED_HPA_LOAD',reason,run_id:p.run_id,binding:p.binding,control_sha256:p.control_sha256,
   started_utc:new Date(Date.now()-(now()-started)).toISOString(),finished_utc:new Date().toISOString(),workload:p.workload,
   phases:events,requests:rows.length,in_flight_at_end:inflight,guest_issue_attempts:issueAttempts,guest_issue_without_confirmed_cleanup_handle:issueUnknown,
   guest_sessions_created:sessions.length,guest_sessions_cleanup_confirmed:cleaned,guest_cleanup_unconfirmed:unknown,
   created_member_rows:0,created_rooms:0,no_game_or_wss_capacity_claim:true,control_failure_observed:reason?.includes('GUARD')??false};
  writeFileSync(join(out,'receipt.json'),JSON.stringify(receipt,null,2)+'\n',{mode:0o600,flag:'wx'});
  writeFileSync(join(out,'counts.json'),JSON.stringify({...summary(rows,events),run_id:p.run_id,binding:p.binding,control_sha256:p.control_sha256},null,2)+'\n',{mode:0o600,flag:'wx'});
  const files=['receipt.json','counts.json','requests.jsonl','events.jsonl','generator.jsonl'].filter(x=>existsSync(join(out,x)));
  writeFileSync(join(out,'SHA256SUMS.json'),JSON.stringify(Object.fromEntries(files.map(x=>[x,createHash('sha256').update(readFileSync(join(out,x))).digest('hex')])),null,2)+'\n',{mode:0o600,flag:'wx'});
  return receipt;
 }
}

if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
 try{
  const a={};for(let i=2;i<process.argv.length;i++){const k=process.argv[i];if(k==='--execute')a.execute=true;else{need(['--plan','--out','--abort-file'].includes(k)&&!a[k]&&process.argv[i+1],'KNOWN_UNIQUE_ARGUMENTS');a[k]=process.argv[++i];}}
  need(a['--plan'],'PLAN_REQUIRED');const p=validate(JSON.parse(readFileSync(a['--plan'],'utf8')));
  if(!a.execute)console.log(JSON.stringify({status:'PASS_LOCAL_HPA_LOAD_PLAN_NO_REQUESTS',requests:0}));
  else{need(a['--out'],'NEW_OUTPUT_REQUIRED');let interrupted=false;const stop=()=>{interrupted=true;};process.on('SIGINT',stop);process.on('SIGTERM',stop);
   try{const r=await run(p,resolve(a['--out']),{aborted:()=>interrupted||!!a['--abort-file']&&existsSync(a['--abort-file'])});console.log(JSON.stringify(r));if(r.status!=='COMPLETED_HPA_LOAD')process.exitCode=2;}
   finally{process.off('SIGINT',stop);process.off('SIGTERM',stop);}}
 }catch(e){console.log(JSON.stringify({status:'STOP_HPA_LOAD',reason:/^[A-Z0-9_]+$/.test(e.message)?e.message:e.name,automatic_retry:false}));process.exitCode=2;}
}
