import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,readFileSync,writeFileSync,existsSync,rmSync} from 'node:fs';
import {join,dirname} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {validate,summary,request,run,phases} from './load.mjs';

function plan(){return {schema:1,run_id:'hpa-synthetic',workload:'guest-lobby-and-ranking-reads',origin:'https://seokpan.apps.new.p1.openshiftapps.com',
 binding:{cluster_id:'newcluster012345',namespace_uid:'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',application_uid:'11111111-2222-3333-4444-555555555555'},
 control_sha256:'a'.repeat(64),guard_argv:['existing-guard'],window:{not_before:new Date(Date.now()-1000).toISOString(),not_after:new Date(Date.now()+3600000).toISOString()},
 phases:phases.map(([vus,seconds])=>({vus,seconds})),max_vus:300,think_ms:1000,timeout_ms:5000,stop_error_fraction:.05};}
const feed=p=>({status:'PASS_HPA_GUARD',config_sha256:p.control_sha256,binding:p.binding});

test('exact 35-minute bounded profile and fixed read workload',()=>{const p=plan();validate(p);assert.equal(phases.reduce((a,x)=>a+x[1],0),2100);p.phases[5].vus=1000;assert.throws(()=>validate(p));});
test('deleted target, too short window, shell string guard are rejected',()=>{for(const mode of ['old','time','argv']){const p=plan();if(mode==='old')p.binding.cluster_id='2tejeb2farjaaoef1e74asdf6amrtpu3';if(mode==='time')p.window.not_after=new Date(Date.now()+1000).toISOString();if(mode==='argv')p.guard_argv='ssh command';assert.throws(()=>validate(p));}});
test('no FE, health or arbitrary write route can become the load',()=>{assert.throws(()=>request('https://example.test','/'));assert.throws(()=>request('https://example.test','/api/v1/members','POST'));});
test('HTTP errors, missing responses, semantic errors remain in denominator; zero VU is N/A',()=>{
 const r=summary([{phase:1,method:'GET',path:'/api/v1/rooms',status:200,semantic_ok:true,elapsed_ms:10},{phase:1,method:'GET',path:'/api/v1/rooms',status:null,semantic_ok:false,elapsed_ms:5000},
  {phase:1,method:'GET',path:'/api/v1/rooms',status:503,semantic_ok:false,elapsed_ms:20}],[{phase:0,vus:0,actual_seconds:120},{phase:1,vus:10,actual_seconds:180}]);
 assert.equal(r.routes['1 GET /api/v1/rooms'].N,3);assert.equal(r.routes['1 GET /api/v1/rooms'].transport_or_unknown,1);assert.equal(r.routes['1 GET /api/v1/rooms'].p95_ms,5000);assert.equal(r.phases[0].http_5xx_fraction,null);
});
test('wrong controller binding aborts before any Guest mutation or measured request',async()=>{
 const p=plan(),root=mkdtempSync(join(tmpdir(),'hpa-test-'));
 try{const r=await run(p,join(root,'out'),{checkGuard:async()=>({...feed(p),config_sha256:'b'.repeat(64)}),send:()=>assert.fail('NETWORK')});assert.equal(r.status,'ABORTED_HPA_LOAD');assert.equal(r.requests,0);assert.equal(r.guest_issue_attempts,0);}finally{rmSync(root,{recursive:true,force:true});}
});
test('partial Guest setup is never retried and cleanup uses only owned in-memory handles',async()=>{
 const p=plan(),root=mkdtempSync(join(tmpdir(),'hpa-test-'));let issued=0,deleted=0;
 try{const r=await run(p,join(root,'out'),{checkGuard:async()=>feed(p),send:async(o,path,method,headers)=>{
  if(method==='POST'){issued++;if(issued===3)return {status:null};return {status:201,body:{csrf_token:'TOPSECRET'},cookies:['seokpan_session=TOPSECRET'+issued+'; Path=/']};}
  assert.equal(method,'DELETE');assert.ok(headers.Cookie.startsWith('seokpan_session=TOPSECRET'));deleted++;return {status:204};
 }});assert.equal(r.status,'ABORTED_HPA_LOAD');assert.equal(issued,10);assert.equal(deleted,9);assert.equal(r.in_flight_at_end,0);
 assert.ok(!readFileSync(join(root,'out/receipt.json'),'utf8').includes('TOPSECRET'));assert.equal(r.created_member_rows,0);assert.equal(r.created_rooms,0);
 }finally{rmSync(root,{recursive:true,force:true});}
});
test('operator abort between setup batches stops new Guest setup and leaves no active load',async()=>{
 const p=plan(),root=mkdtempSync(join(tmpdir(),'hpa-test-'));let checks=0;
 try{const r=await run(p,join(root,'out'),{checkGuard:async()=>feed(p),aborted:()=>++checks>1,
  send:async(o,path,method)=>method==='POST'?{status:201,cookies:['seokpan_session=x'],body:{csrf_token:'c'}}:{status:204}});
 assert.equal(r.status,'ABORTED_HPA_LOAD');assert.equal(r.guest_issue_attempts,10);assert.equal(r.guest_sessions_cleanup_confirmed,10);
 }finally{rmSync(root,{recursive:true,force:true});}
});
test('default CLI validates local file and creates no result directory or browser',()=>{
 const root=mkdtempSync(join(tmpdir(),'hpa-test-'));try{const input=join(root,'plan.json');writeFileSync(input,JSON.stringify(plan()));
 const r=spawnSync(process.execPath,[join(dirname(fileURLToPath(import.meta.url)),'load.mjs'),'--plan',input,'--out',join(root,'out')],{encoding:'utf8'});
 assert.equal(r.status,0,r.stdout+r.stderr);assert.equal(JSON.parse(r.stdout).requests,0);assert.equal(existsSync(join(root,'out')),false);
 }finally{rmSync(root,{recursive:true,force:true});}
});
