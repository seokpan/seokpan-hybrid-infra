import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {counts,Reconnects,runFault,FaultMeter,validateFaultPlan,validateTriggerPlan} from './fault-observer.mjs';

test('S F U disjoint with retries outside original N',()=>{
  const rows=[['success',false,null],['http_error',true,null],['unknown',false,null],['success',false,'one']]
    .map(([outcome,definite_rejection,retry_of])=>({phase:'fault',kind:'business_ws',outcome,definite_rejection,retry_of}));
  assert.deepEqual(counts(rows,'fault').business,{N:3,S:1,F:1,U:1,failed_fraction:1/3,unknown_fraction:1/3,non_success_fraction:2/3,retries:1});
});
test('empty denominator null',()=>assert.equal(counts([],'fault').business.non_success_fraction,null));
test('transport failure remains business unknown',()=>{
  const r=counts([{phase:'fault',kind:'business_ws',retry_of:null,outcome:'unknown',definite_rejection:false}],'fault');
  assert.equal(r.business.U,1);assert.equal(r.business.F,0);
});
test('HTTP and retry denominators separate',()=>{
  const base={phase:'fault',kind:'http',method:'GET',route:'/safe'};
  const r=counts([{...base,status:200},{...base,status:null},{...base,status:500,retry_of:'old'}],'fault').http['GET /safe'];
  assert.equal(r.original,2);assert.equal(r.known_retries,1);assert.equal(r.original_non_success_fraction,.5);assert.equal(r.retry_non_success_fraction,1);
});
function page(){const p=new EventEmitter();return p;}
function socket(path='/ws/v1/rooms/r'){const s=new EventEmitter();s.url=()=> 'wss://service.example.com'+path;return s;}
const snapshot=(v,room='r',game='g')=>JSON.stringify({schema_version:1,state_version:v,event_id:'e'+v,event_type:'room.snapshot',room_id:room,game_id:game,
  payload:{room:{room_id:room,state_version:v,status:'PLAYING'},game:{game_id:game,state_version:v,turn_no:1,board:[],vote_aggregation:[]}}});
test('lobby/chat do not become Room disconnect',()=>{
  const p=page(),c=new Reconnects(()=>10);c.attach(p,'a','room');const s=socket('/ws/v1/chat/rooms/r');p.emit('websocket',s);s.emit('close');
  assert.equal(c.summary()[0].status,'NO_DISCONNECT_NOT_ZERO_RECOVERY');
});
test('first closure retained through failed generations',()=>{
  let t=1;const p=page(),c=new Reconnects(()=>t++);c.attach(p,'a','room');
  const s=socket();p.emit('websocket',s);s.emit('framereceived',{payload:snapshot(1)});s.emit('close');
  const first=c.summary()[0].cycles[0].first_closed_ms;
  const s2=socket();p.emit('websocket',s2);s2.emit('close');
  assert.equal(c.summary()[0].cycles[0].first_closed_ms,first);assert.equal(c.summary()[0].status,'NOT_CONVERGED');
});
test('malformed or wrong room snapshot cannot establish ready',()=>{
  const p=page(),c=new Reconnects(()=>1);c.attach(p,'a','room');const s=socket();p.emit('websocket',s);
  s.emit('framereceived',{payload:'{}'});s.emit('framereceived',{payload:snapshot(1).replace('"room_id":"r"','"room_id":"other"')});
  assert.equal(c.clients.get('a').initialValid,false);
});
test('new connection alone is not state convergence',()=>{
  const p=page(),c=new Reconnects(()=>1);c.attach(p,'a','room');const a=socket();p.emit('websocket',a);a.emit('close');
  p.emit('websocket',socket());assert.equal(c.summary()[0].cycles[0].converged_ms,null);
});
test('old generation close cannot close new connection',()=>{
  const p=page(),c=new Reconnects(()=>1);c.attach(p,'a','room');const a=socket(),b=socket();p.emit('websocket',a);p.emit('websocket',b);a.emit('close');
  assert.equal(c.clients.get('a').open,true);
});

function plan(scenario){return {schema:1,run_id:'synthetic-'+scenario,scenario,target_origin:'https://service.example.com',
  profile:{clients:6,warmup_seconds:60,measurement_seconds:180},extra_recovery_seconds:5,
  release:{app_source_sha:'c2dad254cc407431092691f9ea5892f8324f1520',backend_digest:'sha256:'+'a'.repeat(64),frontend_digest:'sha256:'+'b'.repeat(64)},
  runtime:{replicas:{backend:3,frontend:3},pool_size:2,max_overflow:0,pool_timeout_seconds:5,config_revision:'config',schema_revision:'schema'},
  evidence:{normal_business_ref:'normal-ref',release_binding_ref:'release-ref',run_window_ref:'window-ref'},
  binding:{cluster_id:'newcluster1234567890',namespace_uid:'00000000-0000-0000-0000-000000000001',application_uid:'00000000-0000-0000-0000-000000000002',source_sha256:'c'.repeat(64),normalized_source_sha256:'d'.repeat(64)},
  window:{not_before:new Date(Date.now()-1000).toISOString(),not_after:new Date(Date.now()+3600000).toISOString()}};}
function synthetic(){
  let t=0;const pages=[],contexts=[];
  const clock={now:()=>t,sleep:async ms=>{t+=ms;await new Promise(r=>setImmediate(r));}};
  const fakeBrowser={newContext:async()=>{const c={route:async()=>{},newPage:async()=>{
    const p=new EventEmitter();p.setDefaultTimeout=()=>{};p.setDefaultNavigationTimeout=()=>{};
    p.getByText=()=>({isVisible:async()=>true});
    p.getByRole=(role,options)=>({innerText:async()=> '투표 기회 1번째',evaluateAll:async()=>[],getAttribute:async()=> 'true',
      isVisible:async()=>options?.name!=='게임 상태 다시 확인',count:async()=>1});pages.push(p);return p;
  }};contexts.push(c);return c;},close:async()=>{}};
  const roomFactory=(ps,ids,meter)=>({pages:ps,ids,setupComplete:false,async setup(){
    for(const p of ps){p.roomId='r'+ids[0];p.gameId='g'+ids[0];const s=socket('/ws/v1/rooms/'+p.roomId);p.s=s;p.emit('websocket',s);s.emit('framereceived',{payload:snapshot(1,p.roomId,p.gameId)});}this.setupComplete=true;
  },async step(){meter.emit({kind:'business_ws',action_id:'a'+t+ids[0],retry_of:null,outcome:'success',intent_started_ms:t,finished_ms:t});},async leave(){}});
  return {clock,pages,fakeBrowser,roomFactory};
}
test('separate baseline observer completes fixed window with synthetic browser only',async()=>{
  const s=synthetic();const r=await runFault(plan('baseline'),{clock:s.clock,launchBrowser:async()=>s.fakeBrowser,roomFactory:s.roomFactory,sample:()=>({})});
  assert.equal(r.receipt.status,'COMPLETED_BOUNDED_FAULT_OBSERVATION_NOT_ACCEPTANCE',JSON.stringify({receipt:r.receipt,counts:r.counts}));assert.ok(r.counts.business.N>0);
  assert.ok(r.receipt.reconnects.every(x=>x.status==='NO_DISCONNECT_NOT_ZERO_RECOVERY'));
});
test('one trigger after +30 and closure convergence retained in synthetic full run',async()=>{
  const s=synthetic();let triggers=0;
  const r=await runFault(plan('rolling'),{clock:s.clock,launchBrowser:async()=>s.fakeBrowser,roomFactory:s.roomFactory,sample:()=>({}),trigger:async()=>{
    triggers++;const p=s.pages[0];p.s.emit('close');const next=socket('/ws/v1/rooms/'+p.roomId);p.emit('websocket',next);next.emit('framereceived',{payload:snapshot(2,p.roomId,p.gameId)});
    return {status:'REQUESTED_ONE_GITOPS_BACKEND_ROLL'};
  }});
  assert.equal(triggers,1);assert.ok(r.receipt.trigger_requested_at_ms-r.receipt.fixed_start_ms>=30000);
  assert.equal(r.receipt.status,'COMPLETED_BOUNDED_FAULT_OBSERVATION_NOT_ACCEPTANCE',JSON.stringify({receipt:r.receipt,counts:r.counts}));
  assert.equal(r.receipt.reconnects[0].status,'CONVERGED');assert.equal(r.receipt.reconnects[0].cycles.length,1);
});
test('lost trigger response makes run incomplete without second trigger',async()=>{
  const s=synthetic();let n=0;
  const r=await runFault(plan('rolling'),{clock:s.clock,launchBrowser:async()=>s.fakeBrowser,roomFactory:s.roomFactory,sample:()=>({}),trigger:async()=>{n++;throw Error('unknown');}});
  assert.equal(n,1);assert.equal(r.receipt.status,'INCOMPLETE_FAULT_OBSERVATION');
});
test('unfinished HTTP retains original denominator and start metadata',async()=>{
  const meter=new FaultMeter({runId:'unfinished',now:()=>500});meter.setPhase('fault');
  meter.http.set({}, {client_id:'a',route:'/safe',method:'GET',phase:'fault',start:100,request_started_ms:100,retry_of:null,retry_classification:'NOT_IDENTIFIABLE'});
  const bundle=await meter.finish();const row=bundle.rows.find(x=>x.kind==='http');
  assert.equal(row.request_started_ms,100);assert.equal(row.status,null);
  assert.equal(counts(bundle.rows,'fault').http['GET /safe'].original,1);
});
test('malformed rolling argv rejected by preflight',()=>{
  assert.throws(()=>validateTriggerPlan({scope:'ONE_BACKEND_ROLL',argv:[]},plan('rolling')),/ARGV/);
});
test('other namespace trigger rejected before workload creation',()=>{
  assert.throws(()=>validateTriggerPlan({scope:'ONE_BACKEND_ROLL',argv:['python','script'],binding:{namespace_uid:'other'}},plan('rolling')),/BINDING/);
});
test('unsupported fault profile rejected by the same local validator',()=>{
  const p=plan('baseline');p.profile.clients=12;assert.throws(()=>validateFaultPlan(p),/SIX_CLIENT/);
});
test('different game on same Room cannot establish convergence',()=>{
  const p=page(),c=new Reconnects(()=>1);c.attach(p,'a','room');const a=socket();p.emit('websocket',a);a.emit('framereceived',{payload:snapshot(1)});a.emit('close');
  const b=socket();p.emit('websocket',b);b.emit('framereceived',{payload:snapshot(2,'r','other-game')});
  assert.equal(c.summary()[0].status,'INVALID_ROOM_OR_GAME_BINDING');
});
test('wrong Room HTTP state ignored even with newer version',async()=>{
  const p=page(),c=new Reconnects(()=>1);c.attach(p,'a','room');const a=socket();p.emit('websocket',a);a.emit('framereceived',{payload:snapshot(1)});
  p.emit('response',{status:()=>200,url:()=> 'https://service.example.com/api/v1/rooms/other/state',json:async()=>JSON.parse(snapshot(9,'other')).payload});
  await new Promise(r=>setImmediate(r));assert.equal(c.clients.get('a').snapshot.game_version,1);
});
test('two cohorts cannot silently share one actual room',()=>{
  const a=page(),b=page(),c=new Reconnects(()=>1);c.attach(a,'a','room1');c.attach(b,'b','room2');a.emit('websocket',socket());b.emit('websocket',socket());
  assert.equal(c.clients.get('b').protocolInvalid,true);
});
test('null board entry does not throw or accept invalid first frame',()=>{
  const p=page(),c=new Reconnects(()=>1);c.attach(p,'a','room');const a=socket();p.emit('websocket',a);
  const value=JSON.parse(snapshot(1));value.payload.game.board=[null];
  assert.doesNotThrow(()=>a.emit('framereceived',{payload:JSON.stringify(value)}));assert.equal(c.clients.get('a').initialValid,false);
});
