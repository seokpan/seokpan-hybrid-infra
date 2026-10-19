// New Cloud adapter contracts only. No service, browser or network is contacted.
import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {mkdtempSync,writeFileSync,readFileSync,existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {APP_SOURCE,validatePlan,targetRequestAllowed,targetSocketAllowed,parseTurn,
  coordinates,openPanel,BrowserRoom,runCloud,safeFailure} from './cloud-business.mjs';
import {parseArgs,main} from './run-cloud-measurement.mjs';

function plan() {
  const now=Date.now();return {schema:1,run_id:'new-cloud-adapter-fixture',target_origin:'https://game.fixture.example',
    profile:{clients:6,warmup_seconds:15,measurement_seconds:60},
    release:{app_source_sha:APP_SOURCE,backend_digest:'sha256:'+'a'.repeat(64),frontend_digest:'sha256:'+'b'.repeat(64)},
    runtime:{replicas:{backend:3,frontend:3},pool_size:2,max_overflow:0,pool_timeout_seconds:5,
      config_revision:'fixture-config-hash',schema_revision:'20260902_0002'},
    evidence:{normal_business_ref:'fixture-business-ref',release_binding_ref:'fixture-release-ref',run_window_ref:'fixture-window-ref'},
    window:{not_before:new Date(now-60000).toISOString(),not_after:new Date(now+1800000).toISOString()}};
}
class FakePage extends EventEmitter {
  setDefaultTimeout(){}setDefaultNavigationTimeout(){}
}
class FakeBrowser {
  constructor(){this.contexts=[];this.closed=false;}
  async newContext(options){
    assert.equal(options.ignoreHTTPSErrors,false);assert.equal(options.acceptDownloads,false);
    const page=new FakePage(),context={page,options,route:async(_p,fn)=>{context.guard=fn;},newPage:async()=>page,
      close:async()=>{if(page.socket&&!page.socket.closed){page.socket.closed=true;page.socket.emit('close');}}};
    this.contexts.push(context);return context;
  }
  async close(){this.closed=true;await Promise.all(this.contexts.map(c=>c.close()));}
}
function fixture({failSetup=false,failNormal=false,missingSocket=false,missingFrame=false,unknownVotes=false,missingLatency=false,clients=6}={}) {
  const p=plan();p.profile.clients=clients;let tick=0,cleanup=0,normalSteps=0,launches=0;
  const browser=new FakeBrowser(),clock={now:()=>tick,sleep:async ms=>{tick+=ms;}};
  const roomFactory=(pages,ids,meter,counters)=>({setupComplete:false,emitted:false,
    async setup(){
      if(failSetup&&ids[0]==='client-1')throw Object.assign(Error('SETUP_FAILED'),{name:'CloudCheck'});
      for(const page of pages){if(missingSocket&&ids[0]==='client-1')continue;
        const socket=new EventEmitter();socket.url=()=>p.target_origin.replace('https:','wss:')+'/ws/v1/rooms/fixture-'+ids[0];
        page.socket=socket;page.emit('websocket',socket);if(!missingFrame)socket.emit('framereceived',{payload:'{}'});}
      counters.member_create_attempts+=2;counters.members_confirmed+=2;counters.guests_confirmed++;
      counters.room_create_attempts++;counters.rooms_confirmed++;this.setupComplete=true;
    },
    async step(){if(meter.phase!=='normal')return false;normalSteps++;
      if(failNormal&&ids[0]==='client-1')throw Object.assign(Error('NORMAL_FAILED'),{name:'CloudCheck'});
      if(!this.emitted){this.emitted=true;meter.emit({kind:'business_ws',action_id:'action-'+ids[0],retry_of:null,
        outcome:unknownVotes?'unknown':'success',accepted_http:true,
        command_to_ws_receive_ms:unknownVotes||missingLatency?null:3,
        duration_ms:unknownVotes||missingLatency?null:5,
        client_id:ids[0],peer_id:ids[1],expected_negative:false});
        meter.emit({kind:'http',method:'PUT',route:'/api/v1/games/{game_id}/turns/{turn_no}/vote',status:200,outcome:'success',duration_ms:3});}
      return true;
    },
    async leave(){cleanup++;return {attempted:3,confirmed:3,unconfirmed:0};}
  });
  return {plan:p,browser,clock,roomFactory,sample:()=>({system_cpu_percent:1,system_memory_used_percent:10,node_rss_mib:20}),
    launchBrowser:async()=>{launches++;return browser;},counts:()=>({cleanup,normalSteps,launches})};
}

test('ready nonsecret plan is a local assertion without execution',()=>{
  const x=validatePlan(plan());assert.equal(x.status,'PASS_LOCAL_PLAN_NOT_EXECUTED');assert.equal(x.network_requests,0);
});
test('placeholder target remains pending',()=>{
  const p=plan();p.target_origin='https://input-required.invalid';assert(validatePlan(p).missing.includes('actual_cloud_origin'));
});
test('target URL cannot contain credentials, HTTP, port, path or query',()=>{
  for(const u of ['http://game.example','https://user:password@game.example','https://game.example:8443',
    'https://game.example/path','https://game.example/?token=secret']){
    const p=plan();p.target_origin=u;assert.throws(()=>validatePlan(p));}
});
test('wrong app source does not silently adopt latest main',()=>{
  const p=plan();p.release.app_source_sha='f'.repeat(40);assert.throws(()=>validatePlan(p),/PINNED_UI/);
});
test('maximum-load profile is not enabled by the small-run adapter',()=>{
  const p=plan();p.profile.clients=60;assert.throws(()=>validatePlan(p),/BOUNDED_SIX/);
});
test('normal evidence and current window are required',()=>{
  const p=plan();p.evidence.normal_business_ref=null;p.window.not_after='2020-01-01T00:00:00Z';
  const x=validatePlan(p);assert(x.missing.includes('normal_business_ref'));assert(x.missing.includes('current_window_with_setup_margin'));
});
test('schema, config and operating Pool fields must be actual inputs',()=>{
  const p=plan();p.runtime.schema_revision=null;p.runtime.config_revision=null;p.runtime.pool_timeout_seconds=null;
  const x=validatePlan(p);assert(x.missing.includes('actual_schema_revision'));assert(x.missing.includes('actual_config_revision'));
  assert(x.missing.includes('actual_pool_tuple'));
});
test('unexpected runtime properties cannot be copied into receipts',()=>{
  const p=plan();p.runtime.password='do-not-export';assert.throws(()=>validatePlan(p),/METADATA_FIELDS_ONLY/);
});
test('same-origin HTTP routing allows normal UI APIs and denies outside origins',()=>{
  const o='https://game.fixture.example';assert(targetRequestAllowed(o+'/api/v1/rooms',o,'POST'));
  assert(targetRequestAllowed(o+'/assets/app.js',o));assert(!targetRequestAllowed('https://other.example/api/v1/rooms',o,'POST'));
  assert(!targetRequestAllowed(o+'/unrelated-mutation',o,'POST'));
});
test('all pinned UI socket paths remain allowed over WSS',()=>{
  const o='https://game.fixture.example';for(const p of ['/presence','/lobby','/rooms/abc','/chat/lobby','/chat/rooms/abc'])
    assert(targetSocketAllowed('wss://game.fixture.example/ws/v1'+p,o),p);
});
test('socket queries, plain WS and different hosts are rejected',()=>{
  const o='https://game.fixture.example';for(const u of ['ws://game.fixture.example/ws/v1/lobby',
    'wss://other.example/ws/v1/lobby','wss://game.fixture.example/ws/v1/rooms/abc?token=secret'])assert(!targetSocketAllowed(u,o));
});
test('turn parser uses turn number rather than official move or team color',()=>{
  assert.equal(parseTurn('투표 기회 12번째 · 공식 착수 9수'),12);assert.equal(parseTurn('공식 착수 12수'),null);
});
test('bounded coordinate sets are disjoint and never adjacent in four line directions',()=>{
  const black=coordinates('BLACK'),white=coordinates('WHITE');assert.equal(new Set([...black,...white]).size,black.length+white.length);
  for(const group of [black,white])for(const a of group)for(const b of group){if(a===b)continue;
    assert(/^[A-O](?:[1-9]|1[0-5])$/.test(a));const dx=Math.abs(a.charCodeAt(0)-b.charCodeAt(0)),dy=Math.abs(Number(a.slice(1))-Number(b.slice(1)));
    assert(!((dx===1&&dy===0)||(dx===0&&dy===1)||(dx===1&&dy===1)));}
});
test('panel is pinned even when transient aria-expanded is already true',async()=>{
  const calls=[],trigger={getAttribute:async()=> 'true',focus:async()=>calls.push('focus'),press:async x=>calls.push(x)};
  await openPanel({getByRole:()=>trigger},'실시간 투표 현황');assert.deepEqual(calls,['focus','Escape','Enter']);
});
test('cleanup follows Guest then non-host then host and is not repeated',async()=>{
  const order=[];const make=name=>({getByRole:(_role,opts)=>({isVisible:async()=>true,click:async()=>order.push(name+':'+opts.name),waitFor:async()=>{}})});
  const pages=[make('black'),make('white'),make('guest')],room=new BrowserRoom(pages,['a','b','c'],{},{});
  pages.forEach(p=>room.joined.add(p));const x=await room.leave();assert.equal(x.confirmed,3);
  assert.deepEqual(order,['guest:방 나가기','white:방 나가기','black:방 나가기']);
  const y=await room.leave();assert.equal(y.reason,'ALREADY_ATTEMPTED_NO_RETRY');assert.equal(order.length,3);
});
test('partial setup can leave only its own confirmed page without setupComplete',async()=>{
  const order=[],make=name=>({getByRole:(_role,opts)=>({isVisible:async()=>false,click:async()=>order.push(name),waitFor:async()=>{}})});
  const pages=[make('black'),make('white'),make('guest')],room=new BrowserRoom(pages,['a','b','c'],{},{});
  room.joined.add(pages[0]);const x=await room.leave();assert.equal(x.confirmed,1);assert.deepEqual(order,['black']);
});
test('invalid inputs stop before browser adapter invocation',async()=>{
  const f=fixture();f.plan.evidence.normal_business_ref=null;
  await assert.rejects(runCloud(f.plan,f),/READY_EXECUTION/);assert.equal(f.counts().launches,0);
});
test('six-context fixture connects the new adapter to reused measurements and scoped summary',async()=>{
  const f=fixture(),result=await runCloud(f.plan,f);
  assert.equal(result.receipt.status,'COMPLETED_SCOPED_CLOUD_OBSERVATION_NOT_FULL_ACCEPTANCE');
  assert.equal(result.receipt.room_socket_connections_at_barrier,6);assert.equal(result.receipt.created_contexts,6);
  assert.equal(result.summary.business_ws.first_attempts,2);assert.equal(result.summary.status,'NOT_RUNTIME_ACCEPTANCE');
  assert.equal(result.receipt.normal_vote_correlated_successes,2);
  assert.deepEqual([result.summary.cloud_vote_latency.ui_action_to_peer_ws_p95_ms,
    result.summary.cloud_vote_latency.ui_action_to_peer_ui_p95_ms],[3,5]);
  assert.equal(result.receipt.harness_vote_retries,0);assert.equal(result.receipt.member_rows_deleted,false);
  assert.equal(f.counts().cleanup,2);assert(f.browser.closed);
});
test('twelve-context fixture creates four rooms rather than shared-cookie tabs',async()=>{
  const f=fixture({clients:12}),r=await runCloud(f.plan,f);assert.equal(r.receipt.room_socket_connections_at_barrier,12);
  assert.equal(r.receipt.rooms_setup_complete,4);assert.equal(f.browser.contexts.length,12);
});
test('all unknown votes cannot become a completed latency observation',async()=>{
  const f=fixture({unknownVotes:true}),r=await runCloud(f.plan,f);
  assert.equal(r.receipt.status,'INCOMPLETE_CLOUD_OBSERVATION');assert.equal(r.receipt.normal_vote_first_attempts,2);
  assert.equal(r.receipt.normal_vote_correlated_successes,0);assert.equal(r.receipt.correlated_latency_missing,true);
  assert.equal(r.summary.cloud_vote_latency.ui_action_to_peer_ws_p95_ms,null);assert(f.browser.closed);
});
test('success labels without correlated timings cannot satisfy observation completion',async()=>{
  const f=fixture({missingLatency:true}),r=await runCloud(f.plan,f);
  assert.equal(r.receipt.status,'INCOMPLETE_CLOUD_OBSERVATION');assert.equal(r.receipt.normal_vote_correlated_successes,0);
  assert.equal(r.summary.cloud_vote_latency.ui_action_to_peer_ui_p95_ms,null);assert(f.browser.closed);
});
test('missing room sockets cannot become claimed six-client observation',async()=>{
  const f=fixture({missingSocket:true}),r=await runCloud(f.plan,f);assert.equal(r.receipt.status,'INCOMPLETE_CLOUD_OBSERVATION');
  assert.equal(r.receipt.failure.reason,'FULL_ROOM_SOCKET_BARRIER_REQUIRED');assert.equal(r.receipt.normal_duration_seconds,0);
});
test('created socket objects without received frames cannot satisfy the cohort',async()=>{
  const f=fixture({missingFrame:true}),r=await runCloud(f.plan,f);assert.equal(r.receipt.status,'INCOMPLETE_CLOUD_OBSERVATION');
  assert.equal(r.receipt.failure.reason,'EACH_ROOM_CLIENT_RECEIVED_FRAME_REQUIRED');assert.equal(r.receipt.normal_duration_seconds,0);
});
test('partial setup is reported incomplete and owned browser is closed',async()=>{
  const f=fixture({failSetup:true}),r=await runCloud(f.plan,f);assert.equal(r.receipt.status,'INCOMPLETE_CLOUD_OBSERVATION');
  assert.equal(r.receipt.failure.stage,'setup');assert(f.browser.closed);assert.equal(f.counts().cleanup,2);
});
test('normal failure settles sibling loops and still attempts own cleanup once',async()=>{
  const f=fixture({failNormal:true}),r=await runCloud(f.plan,f);assert.equal(r.receipt.status,'INCOMPLETE_CLOUD_OBSERVATION');
  assert.equal(r.receipt.failure.stage,'normal');assert.equal(f.counts().cleanup,2);assert(f.browser.closed);
});
test('raw Playwright errors and credentials never become a failure reason',()=>{
  const e=Error('https://user:secret@private.example raw body csrf_token');assert.equal(safeFailure(e),'Error');
});
test('CLI is non-executing by default and rejects unknown unsafe options',()=>{
  assert.equal(parseArgs(['--plan','input.json']).execute,false);
  assert.throws(()=>parseArgs(['--plan','input.json','--ignore-https-errors']));
});
test('local CLI check does not load Playwright or create output',async()=>{
  const dir=mkdtempSync(join(tmpdir(),'cloud-new-gate-')),file=join(dir,'plan.json'),p=plan();p.evidence.normal_business_ref=null;
  writeFileSync(file,JSON.stringify(p));let launched=false;const original=console.log;console.log=()=>{};
  try{assert.equal(await main(['--plan',file],{playwright:{chromium:{launch:async()=>{launched=true;}}}}),2);}
  finally{console.log=original;}assert.equal(launched,false);assert(!existsSync(join(dir,'results')));
});
test('CLI fixture writes only bounded sanitized reports after explicit execute',async()=>{
  const dir=mkdtempSync(join(tmpdir(),'cloud-new-cli-')),file=join(dir,'plan.json'),out=join(dir,'results'),f=fixture();
  writeFileSync(file,JSON.stringify(f.plan));const original=console.log;console.log=()=>{};
  try{assert.equal(await main(['--plan',file,'--out',out,'--execute'],{
    playwright:{chromium:{launch:f.launchBrowser}},runtime:{clock:f.clock,roomFactory:f.roomFactory,sample:f.sample}}),0);}
  finally{console.log=original;}
  const receipt=JSON.parse(readFileSync(join(out,'run-receipt.json'),'utf8'));
  assert.equal(receipt.deployment_independently_queried,false);assert(existsSync(join(out,'SHA256SUMS.json')));
  assert(!JSON.stringify(receipt).includes('password'));assert(!JSON.stringify(receipt).includes('csrf_token'));
});
