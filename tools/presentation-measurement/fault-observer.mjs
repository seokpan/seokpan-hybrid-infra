// Separate observer. The accepted normal runCloud and ROOM_SOCKET_COHORT_LOST are unchanged.
import {performance} from 'node:perf_hooks';
import {createHash} from 'node:crypto';
import os from 'node:os';
import {BrowserRoom,validatePlan,check,openPanel,targetRequestAllowed,targetSocketAllowed,realClock,resourceSampler} from './vendor/cloud-business.mjs';
import {Measurement} from './vendor/measurement.mjs';

export function counts(rows,phase) {
  const selected=rows.filter(r=>r.phase===phase);
  const original=selected.filter(r=>r.kind==='business_ws'&&r.retry_of===null&&!r.expected_negative);
  const S=original.filter(r=>r.outcome==='success').length;
  const F=original.filter(r=>r.definite_rejection===true&&r.outcome!=='success').length;
  const N=original.length,U=N-S-F;
  const groups={};
  for(const r of selected.filter(r=>r.kind==='http'&&!r.expected_negative)){
    const a=(groups[r.method+' '+r.route]??={original:0,known_retries:0,retry_classification_unknown:0,original_non_success:0,retry_non_success:0,unexpected_status:0,transport_or_no_response:0});
    if(r.retry_of)a.known_retries++;else a.original++;
    if(r.retry_classification==='NOT_IDENTIFIABLE')a.retry_classification_unknown++;
    if(r.status!==null&&(r.status<200||r.status>=300))a.unexpected_status++;
    if(r.status===null)a.transport_or_no_response++;
    if(r.status===null||r.status<200||r.status>=300){if(r.retry_of)a.retry_non_success++;else a.original_non_success++;}
  }
  for(const x of Object.values(groups)){
    x.original_non_success_fraction=x.original?x.original_non_success/x.original:null;
    x.retry_non_success_fraction=x.known_retries?x.retry_non_success/x.known_retries:null;
  }
  return {business:{N,S,F,U,failed_fraction:N?F/N:null,unknown_fraction:N?U/N:null,
    non_success_fraction:N?(F+U)/N:null,retries: selected.filter(r=>r.kind==='business_ws'&&r.retry_of!==null).length},http:groups};
}

export class FaultMeter extends Measurement {
  constructor(options){super(options);this.rejections=new Map();this.requests=new Map();this.intent=0;this.intentStarts=new Map();}
  emit(row){
    if(row.kind==='business_ws')row={...row,definite_rejection:this.rejections.get(row.action_id)===true,
      intent_started_ms:this.intentStarts.get(row.action_id)??row.intent_started_ms,finished_ms:this.now()};
    if(row.kind==='http')row={...row,finished_ms:this.now()};
    super.emit(row);
  }
  attach(page,clientId){
    super.attach(page,clientId);
    const request=r=>{
      const item=this.http.get(r);if(!item)return;item.request_started_ms=this.now();
      let id=null;
      if(r.method()==='PUT')try{id=r.postDataJSON()?.request_id;}catch{}
      if(typeof id==='string'){
        const ref=this.pseudonym(id),old=this.requests.get(ref);
        Object.assign(item,{retry_of:old??null,retry_classification:'REQUEST_ID_REUSE',request_ref:ref});
        if(!old)this.requests.set(ref,ref);
      }else Object.assign(item,{retry_of:null,retry_classification:'NOT_IDENTIFIABLE'});
    };
    page.on('request',request);this.listeners.push(()=>page.off('request',request));
  }
  async measureVote(options){
    const actionId='intent-'+(++this.intent);this.intentStarts.set(actionId,this.now());
    const onResponse=async r=>{
      try{
        if(r.request().method()!=='PUT'||!new URL(r.url()).pathname.match(/\/games\/[^/]+\/turns\/\d+\/vote$/))return;
        if(r.request().postDataJSON()?.coordinate!==options.coordinate)return;
        // Only explicit stale-state rejection is classified F. 5xx, timeout, malformed
        // and other ambiguous application responses remain U; never retry a vote.
        if(r.status()===409){const v=await r.json();
          if(v?.error?.code==='STALE_STATE')this.rejections.set(actionId,true);}
      }catch{}
    };
    options.actor.on('response',onResponse);
    try{await super.measureVote({...options,actionId});}
    finally{options.actor.off('response',onResponse);}
  }
  async finish(){
    this.listeners.splice(0).forEach(fn=>fn());
    await Promise.all([...this.pending]);
    for(const item of this.http.values())this.emit({...item,kind:'http',status:null,outcome:'unknown',
      duration_ms:null,observer_duration_ms:this.now()-item.start,boundary:'unfinished_at_collection_end'});
    this.http.clear();return super.finish();
  }
}

export function validateFaultPlan(plan){
  const r=validatePlan(plan);check(!r.missing.length,'CURRENT_BOUND_MEASUREMENT_PLAN_REQUIRED');
  check(plan.profile.clients===6&&plan.profile.warmup_seconds===60&&plan.profile.measurement_seconds===180,'SIX_CLIENT_TWO_ROOM_60_180_PROFILE_REQUIRED');
  check(['baseline','rolling'].includes(plan.scenario),'BASELINE_OR_ROLLING_REQUIRED');
  const uuid=/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
  check(/^[a-z0-9]{10,64}$/.test(plan.binding?.cluster_id??'')&&plan.binding.cluster_id!=='2tejeb2farjaaoef1e74asdf6amrtpu3'
    &&uuid.test(plan.binding?.namespace_uid??'')&&uuid.test(plan.binding?.application_uid??'')
    &&/^[a-f0-9]{64}$/.test(plan.binding?.source_sha256??'')
    &&/^[a-f0-9]{64}$/.test(plan.binding?.normalized_source_sha256??''),'NEW_PLATFORM_BINDING_REQUIRED');
  check(Number.isInteger(plan.extra_recovery_seconds)&&plan.extra_recovery_seconds>=0&&plan.extra_recovery_seconds<=120,'BOUNDED_EXTRA_RECOVERY_REQUIRED');
  return r;
}

export function validateTriggerPlan(value,plan){
  check(Array.isArray(value?.argv)&&value.argv.length>=2&&value.argv.every(x=>typeof x==='string'&&x.length>0),'ARGV_NO_SHELL_REQUIRED');
  check(value.scope==='ONE_BACKEND_ROLL'&&value.binding?.application_uid===plan.binding.application_uid
    &&value.binding?.namespace_uid===plan.binding.namespace_uid,'TRIGGER_PLAN_UID_BINDING_REQUIRED');
  return value;
}

function validGame(g){
  return !!g&&typeof g.game_id==='string'&&Number.isSafeInteger(g.state_version)&&g.state_version>=1
    &&Number.isSafeInteger(g.turn_no)&&g.turn_no>=1&&Array.isArray(g.board)&&g.board.length<=225
    &&g.board.every(c=>c&&/^[A-O](?:[1-9]|1[0-5])$/.test(c.coordinate)&&['BLACK','WHITE'].includes(c.stone))
    &&new Set(g.board.map(c=>c.coordinate)).size===g.board.length
    &&Array.isArray(g.vote_aggregation)&&g.vote_aggregation.every(c=>c&&/^[A-O](?:[1-9]|1[0-5])$/.test(c.coordinate)&&Number.isSafeInteger(c.count)&&c.count>=1)
    &&new Set(g.vote_aggregation.map(c=>c.coordinate)).size===g.vote_aggregation.length;
}

export class Reconnects {
  constructor(now){this.now=now;this.clients=new Map();this.latest=new Map();this.rows=[];this.roomIds=new Map();this.gameIds=new Map();}
  attach(page,id,roomRef){
    const client={id,roomRef,generation:0,open:false,initialValid:false,protocolInvalid:false,roomId:null,firstClose:null,cycles:[],snapshot:null,receivedAt:null,connectedAt:null};
    this.clients.set(id,client);
    page.on('websocket',socket=>{
      const path=new URL(socket.url()).pathname;
      if(!/^\/ws\/v1\/rooms\/[^/]+$/.test(path))return;
      const roomId=path.split('/').at(-1),known=this.roomIds.get(roomRef);
      if((known&&known!==roomId)||[...this.roomIds.entries()].some(([ref,rid])=>ref!==roomRef&&rid===roomId)){client.protocolInvalid=true;return;}
      this.roomIds.set(roomRef,roomId);client.roomId=roomId;
      const generation=++client.generation;
      client.open=true;client.initialValid=false;client.snapshot=null;client.receivedAt=null;client.connectedAt=null;
      this.rows.push({kind:'room_socket_created',client:id,room:roomRef,generation,monotonic_ms:this.now()});
      socket.on('framereceived',({payload})=>{
        if(typeof payload!=='string'||payload.length>131072||generation!==client.generation)return;
        let v;try{v=JSON.parse(payload);}catch{return;}
        if(v?.schema_version!==1||!Number.isSafeInteger(v.state_version)||v.state_version<1
          ||typeof v.event_id!=='string'||typeof v.event_type!=='string'||!v.payload)return;
        if(v.room_id!==roomId)return;
        if(v.event_type==='room.snapshot'&&v.payload.room?.room_id===roomId){
          const g=v.payload.game;
          if(!Number.isSafeInteger(v.payload.room.state_version)||v.payload.room.state_version<1)return;
          if(!g&&v.payload.room.status==='WAITING'){client.initialValid=true;return;}
          if(!validGame(g))return;
          const game=this.gameIds.get(roomRef);
          if(game&&game!==g.game_id){client.protocolInvalid=true;return;}
          this.gameIds.set(roomRef,g.game_id);client.initialValid=true;
          client.snapshot={room_version:v.payload.room.state_version,stream_version:v.state_version,
            game_version:g.state_version,turn:g.turn_no,board:g.board.map(c=>({coordinate:c.coordinate,stone:c.stone})),tally:g.vote_aggregation??[]};
          client.receivedAt=this.now();client.connectedAt??=this.now();
          this.latest.set(roomRef,Math.max(this.latest.get(roomRef)??0,g.state_version));
          this.rows.push({kind:'room_snapshot_received',client:id,room:roomRef,generation,monotonic_ms:this.now(),
            stream_version:v.state_version,game_version:g.state_version});
        }else if(Number.isSafeInteger(v.payload.game_state_version)&&v.room_id===roomId){
          const game=this.gameIds.get(roomRef);
          if(game&&v.game_id!==game){client.protocolInvalid=true;return;}
          if(v.game_id)this.gameIds.set(roomRef,v.game_id);
          this.latest.set(roomRef,Math.max(this.latest.get(roomRef)??0,v.payload.game_state_version));
        }
      });
      socket.on('close',()=>{
        if(generation!==client.generation)return;
        const at=this.now();client.open=false;
        if(client.firstClose===null){client.firstClose=at;client.cycles.push({first_closed_ms:at,closed_generation:generation,
          new_connection_ms:null,converged_ms:null,new_generation:null,game_version:null});}
        this.rows.push({kind:'room_socket_closed',client:id,room:roomRef,generation,monotonic_ms:at});
      });
    });
    page.on('response',async response=>{
      try{
        const c=client,generation=c.generation;
        if(!c.open||!c.initialValid||response.status()!==200||!/^\/api\/v1\/rooms\/[^/]+\/state$/.test(new URL(response.url()).pathname))return;
        if(new URL(response.url()).pathname!=='/api/v1/rooms/'+c.roomId+'/state')return;
        const v=await response.json(),g=v.game;
        if(generation!==c.generation||!c.open||v.room?.room_id!==c.roomId||!validGame(g))return;
        const game=this.gameIds.get(roomRef);
        if(game&&game!==g.game_id){c.protocolInvalid=true;return;}
        this.gameIds.set(roomRef,g.game_id);
        c.snapshot={room_version:v.room?.state_version,stream_version:v.stream_version,game_version:g.state_version,turn:g.turn_no,
          board:g.board.map(x=>({coordinate:x.coordinate,stone:x.stone})),tally:g.vote_aggregation??[]};
        c.receivedAt=this.now();this.latest.set(roomRef,Math.max(this.latest.get(roomRef)??0,g.state_version));
      }catch{}
    });
  }
  resetWindow(){for(const c of this.clients.values()){c.firstClose=null;c.cycles=[];}this.rows=[];}
  async converge(page,id){
    const c=this.clients.get(id),cycle=c.cycles.at(-1),snap=c.snapshot;
    if(c.firstClose===null||c.protocolInvalid||!c.open||!snap||c.generation<=cycle.closed_generation
      ||snap.game_version<(this.latest.get(c.roomRef)??0))return false;
    try{
      const status=await page.getByRole('region',{name:'현재 투표 상태'}).innerText({timeout:300});
      if(!status.includes('투표 기회 '+snap.turn+'번째'))return false;
      for(const cell of snap.board){
        const stone=cell.stone==='BLACK'?'흑돌':'백돌';
        if(await page.getByRole('button',{name:new RegExp('^'+cell.coordinate+' '+stone+'(?:,|$)')}).count()!==1)return false;
      }
      const occupied=await page.getByRole('button').evaluateAll(buttons=>buttons.map(x=>x.getAttribute('aria-label')||'')
        .filter(x=>/^[A-O](?:[1-9]|1[0-5]) (?:흑돌|백돌)(?:,|$)/.test(x)));
      if(occupied.length!==snap.board.length)return false;
      const panel=page.getByRole('button',{name:/실시간 투표 현황/});
      if(await panel.getAttribute('aria-expanded')!=='true')await openPanel(page,'실시간 투표 현황');
      if(snap.tally.length){
        const list=page.getByRole('list',{name:'좌표별 득표 순위'});
        if(await list.getByRole('listitem').count()!==snap.tally.length)return false;
        for(const tally of snap.tally){
          if(!Number.isSafeInteger(tally.count)||tally.count<1)return false;
          const row=list.getByRole('listitem').filter({has:page.getByText(tally.coordinate,{exact:true})});
          if(!new RegExp(tally.count+'표(?:\\s|$)').test(await row.innerText({timeout:300})))return false;
        }
      }else if(!await page.getByText('아직 제출된 표가 없습니다.',{exact:true}).isVisible())return false;
      if(await page.getByRole('button',{name:'게임 상태 다시 확인',exact:true}).isVisible())return false;
      // Bind the latest received valid game version to its UI turn/occupied cells.
      // The UI does not expose a version attribute; do not claim direct DOM version equality.
      if(c.snapshot!==snap||snap.game_version<(this.latest.get(c.roomRef)??0))return false;
      cycle.new_connection_ms=c.connectedAt;cycle.new_generation=c.generation;
      cycle.converged_ms=this.now();cycle.game_version=snap.game_version;
      cycle.evidence='initial WSS room.snapshot followed by latest valid snapshot version >= observed peers; UI turn, occupied board and tally match';
      this.rows.push({kind:'client_converged',client:id,room:c.roomRef,generation:c.generation,monotonic_ms:cycle.converged_ms,
        game_version:snap.game_version,first_closed_ms:cycle.first_closed_ms});
      c.firstClose=null;return true;
    }catch{return false;}
  }
  summary(){return [...this.clients.values()].map(c=>({client:c.id,room:c.roomRef,
    status:c.protocolInvalid?'INVALID_ROOM_OR_GAME_BINDING':c.cycles.length?c.firstClose===null?'CONVERGED':'NOT_CONVERGED':'NO_DISCONNECT_NOT_ZERO_RECOVERY',
    cycles:c.cycles.map(v=>({...v,first_close_to_converged_ms:v.converged_ms===null?null:v.converged_ms-v.first_closed_ms})),
    current_generation:c.generation,room_socket_open:c.open}));}
}

export async function runFault(plan,{launchBrowser,trigger,clock=realClock,roomFactory,sample=resourceSampler(),abortRequested=()=>false}={}){
  const ready=validateFaultPlan(plan);
  check(plan.scenario==='baseline'||typeof trigger==='function','ONE_ROLLING_TRIGGER_ADAPTER_REQUIRED');
  const meter=new FaultMeter({runId:plan.run_id,now:clock.now}),connections=new Reconnects(clock.now);
  const rooms=[],contexts=[],pages=[],resources=[],counters={member_create_attempts:0,members_confirmed:0,guests_confirmed:0,room_create_attempts:0,rooms_confirmed:0};
  let browser,failure=null,phase='setup',start=null,end=null,triggerAt=null,triggerTask=null,triggerResult=null,aborting=false,setupTimer;
  const fixedEnd=()=>start+180000;
  const bounded=()=>{check(!abortRequested(),'OPERATOR_ABORT');check(Date.now()<Date.parse(plan.window.not_after),'RUN_WINDOW_EXPIRED');
    check([...connections.clients.values()].every(c=>!c.protocolInvalid),'ROOM_OR_GAME_BINDING_CHANGED');};
  try{
    browser=await launchBrowser();setupTimer=setTimeout(()=>{aborting=true;void browser.close().catch(()=>{});},180000);
    for(let i=0;i<6;i++){
      const context=await browser.newContext({baseURL:ready.target_origin,ignoreHTTPSErrors:false,acceptDownloads:false,viewport:{width:1280,height:900}});
      contexts.push(context);const page=await context.newPage();pages.push(page);page.setDefaultTimeout(4000);page.setDefaultNavigationTimeout(15000);
      await context.route('**/*',async route=>{const r=route.request();if(!targetRequestAllowed(r.url(),ready.target_origin,r.method())){
        aborting=true;await route.abort('blockedbyclient');}else await route.continue();});
      page.on('pageerror',()=>{aborting=true;});
      page.on('websocket',s=>{if(!targetSocketAllowed(s.url(),ready.target_origin))aborting=true;});
      meter.attach(page,'client-'+(i+1));connections.attach(page,'client-'+(i+1),'room-'+(Math.floor(i/3)+1));
    }
    for(let i=0;i<2;i++)rooms.push(roomFactory?roomFactory(pages.slice(i*3,i*3+3),['client-'+(i*3+1),'client-'+(i*3+2),'client-'+(i*3+3)],meter,counters,clock):
      new BrowserRoom(pages.slice(i*3,i*3+3),['client-'+(i*3+1),'client-'+(i*3+2),'client-'+(i*3+3)],meter,counters,clock));
    await Promise.all(rooms.map(r=>r.setup()));clearTimeout(setupTimer);
    check([...connections.clients.values()].every(c=>c.open&&c.initialValid),'ALL_ROOM_INITIAL_SNAPSHOTS_REQUIRED');
    const loop=async(until)=>{
      const tasks=await Promise.allSettled(rooms.map(async room=>{
        while(clock.now()<until&&!aborting){bounded();
          const active=room.ids.every(id=>{const c=connections.clients.get(id);return c?.open&&c.firstClose===null;});
          // Disconnected intervals remain part of the denominator/time window. No fake votes.
          if(active)await room.step();
          await clock.sleep(100);
        }
      }));
      const rejected=tasks.find(x=>x.status==='rejected');if(rejected)throw rejected.reason;
      check(!aborting,'GENERATOR_OR_TARGET_FAILURE');
    };
    phase='warmup';await loop(clock.now()+60000);
    check([...connections.clients.values()].every(c=>c.open&&c.firstClose===null),'BASELINE_COHORT_NOT_STABLE_AFTER_WARMUP');
    connections.resetWindow();phase=plan.scenario==='baseline'?'normal':'fault';meter.setPhase(phase);start=clock.now();
    let loopsDone=false;
    const monitor=(async()=>{
      while(!loopsDone&&clock.now()<fixedEnd()){
        bounded();
        for(let i=0;i<pages.length;i++)await connections.converge(pages[i],'client-'+(i+1));
        resources.push({phase,monotonic_ms:clock.now(),...sample()});
        if(plan.scenario==='rolling'&&triggerAt===null&&clock.now()-start>=30000){
          triggerAt=clock.now();meter.emit({kind:'rolling_trigger_requested',monotonic_ms:triggerAt,offset_ms:triggerAt-start});
          triggerTask=Promise.resolve().then(()=>trigger()).then(r=>{triggerResult=r;meter.emit({kind:'rolling_trigger_receipt',monotonic_ms:clock.now(),status:r.status});})
            .catch(()=>{aborting=true;triggerResult={status:'UNKNOWN_TRIGGER_RESULT_RECONCILE_NO_RETRY'};});
        }
        await clock.sleep(500);
      }
    })();
    monitor.catch(()=>{aborting=true;});
    try{await loop(fixedEnd());}finally{loopsDone=true;await monitor;}
    end=clock.now();
    phase='recovery';meter.setPhase('recovery');const extraEnd=Math.min(clock.now()+plan.extra_recovery_seconds*1000,clock.now()+Math.max(0,Date.parse(plan.window.not_after)-Date.now()));
    while(clock.now()<extraEnd&&connections.summary().some(c=>c.status==='NOT_CONVERGED')){
      bounded();for(let i=0;i<pages.length;i++)await connections.converge(pages[i],'client-'+(i+1));await clock.sleep(250);
    }
    if(triggerTask)await triggerTask;
  }catch(e){failure={phase,reason:e?.name==='CloudCheck'?e.message:'GENERATOR_OR_ADAPTER_FAILED'};}
  finally{
    clearTimeout(setupTimer);meter.setPhase('recovery');
    // Cleanup-created socket closes must not become outage samples.
    const observed=connections.summary();connections.finalRows=connections.rows.slice();
    for(const room of rooms)try{await room.leave();}catch{}
    try{await browser?.close();}catch{}
    connections.final=observed;
  }
  const bundle=await meter.finish();
  // Preserve full raw observations; derive cutoff outcomes separately.
  const measurementPhase=plan.scenario==='baseline'?'normal':'fault';
  const fixedRows=bundle.rows.filter(r=>!['business_ws','http'].includes(r.kind)
    ||r.phase!==measurementPhase||(r.intent_started_ms??r.request_started_ms)<fixedEnd()).map(r=>
    ['business_ws','http'].includes(r.kind)&&r.phase===measurementPhase&&r.finished_ms>fixedEnd()
      ?{...r,outcome:'unknown',status:null,definite_rejection:false,outcome_at_cutoff:'IN_FLIGHT',later_outcome:r.outcome}:r);
  const totals=counts(fixedRows,measurementPhase);
  if(!failure&&(bundle.status==='INVALID'||totals.business.N===0||end===null||end<fixedEnd()))failure={phase:'summary',reason:'INCOMPLETE_OR_EMPTY_FIXED_OBSERVATION'};
  return {receipt:{status:failure?'INCOMPLETE_FAULT_OBSERVATION':'COMPLETED_BOUNDED_FAULT_OBSERVATION_NOT_ACCEPTANCE',failure,
    run_id:plan.run_id,binding:plan.binding,release:plan.release,runtime:plan.runtime,
    scenario:plan.scenario,fixed_start_ms:start,fixed_end_ms:start===null?null:fixedEnd(),actual_loop_end_ms:end,
    trigger_requested_at_ms:triggerAt,trigger_result:triggerResult,workload:{clients:6,rooms:2,warmup_seconds:60,comparison_seconds:180},
    extra_recovery_seconds:plan.extra_recovery_seconds,reconnects:connections.final,
    clock:{host:os.hostname(),performance_time_origin_unix_ms:performance.timeOrigin,clock_sync_ref:plan.clock_sync_ref??null,
      cross_host_clock_error_bound_ms:plan.cross_host_clock_error_bound_ms??null},
    generator_failures_are_not_service_failures:true,raw_frames_or_credentials_exported:false,member_rows_deleted:false,
    actual_pod_node_db_timeline:'JOIN_CONTROLLER_OBSERVATION_BY_UTC_WITH_CLOCK_ERROR_BOUND_NOT_BLIND_SUBTRACTION'},
    counts:{...totals,run_id:plan.run_id,binding:plan.binding,phase:measurementPhase},bundle,connections:connections.finalRows,resources};
}

export async function externalCheck(request,{launchBrowser,now=()=>performance.now()}={}){
  check(request.schema===1&&request.nonce&&request.binding,'BOUND_EXTERNAL_REQUEST_REQUIRED');
  const origin=new URL(request.target_origin);check(origin.protocol==='https:'&&origin.origin===request.target_origin,'EXACT_HTTPS_ORIGIN_REQUIRED');
  check(Date.now()<Date.parse(request.window.not_after),'EXTERNAL_WINDOW_EXPIRED');
  const start=now();let browser,frame=false,status=null;
  try{
    browser=await launchBrowser();const context=await browser.newContext({baseURL:origin.origin,ignoreHTTPSErrors:false,acceptDownloads:false});
    await context.route('**/*',async r=>{if(targetRequestAllowed(r.request().url(),origin.origin,r.request().method()))await r.continue();else await r.abort();});
    const page=await context.newPage();page.setDefaultTimeout(15000);
    page.on('websocket',s=>{if(new URL(s.url()).pathname!=='/ws/v1/lobby')return;
      s.on('framereceived',({payload})=>{try{const v=JSON.parse(payload);if(v.schema_version===1&&v.event_type==='lobby.snapshot'&&Number.isSafeInteger(v.state_version)&&v.state_version>=1&&Array.isArray(v.payload?.rooms))frame=true;}catch{}});});
    const response=await page.goto(origin.origin);status=response?.status()??null;check(status===200,'EXPECTED_HTTPS_DOCUMENT_REQUIRED');
    await page.getByRole('button',{name:'Guest로 시작하기 →'}).click();await page.getByRole('heading',{name:'게임 방',exact:true}).waitFor();
    const end=now()+15000;while(!frame&&now()<end)await new Promise(r=>setTimeout(r,100));
    check(frame,'VALID_LOBBY_WSS_INITIAL_SNAPSHOT_REQUIRED');
    return {schema:1,status:'PASS_EXTERNAL_HTTPS_WSS_INITIAL_FRAME',binding:request.binding,nonce:request.nonce,
      target_origin_sha256:createHash('sha256').update(origin.origin).digest('hex'),https_status:status,wss_initial_envelope:true,
      elapsed_ms:now()-start,observed_utc:new Date().toISOString(),guest_session_created:true,accounts_or_rooms_created:0};
  }finally{await browser?.close();}
}
