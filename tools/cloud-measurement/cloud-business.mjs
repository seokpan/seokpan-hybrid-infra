// New same-origin HTTPS/WSS normal-business adapter. No network action on import.
import {randomUUID, createHash} from 'node:crypto';
import {performance} from 'node:perf_hooks';
import os from 'node:os';
import {Measurement, summarize} from './measurement.mjs';

export const APP_SOURCE = 'c2dad254cc407431092691f9ea5892f8324f1520';
export const realClock = {now:()=>performance.now(), sleep:ms=>new Promise(r=>setTimeout(r,ms))};
export const check = (value, code) => {if(!value){const e=new Error(code);e.name='CloudCheck';throw e;}};
export const safeFailure = e => e?.name==='CloudCheck' && /^[A-Z0-9_]+$/.test(e.message)
  ? e.message : (['TimeoutError','TypeError','Error'].includes(e?.name)?e.name:'EXECUTION_FAILED');

export function validatePlan(plan, now=Date.now()) {
  const missing=[];
  check(plan?.schema===1,'PLAN_SCHEMA_REQUIRED');
  check(Object.keys(plan.runtime||{}).every(k=>['replicas','pool_size','max_overflow','pool_timeout_seconds','config_revision','schema_revision'].includes(k)),
    'RUNTIME_METADATA_FIELDS_ONLY');
  const p=plan.profile||{};
  check([6,12].includes(p.clients),'BOUNDED_SIX_OR_TWELVE_CLIENTS_REQUIRED');
  check(Number.isInteger(p.warmup_seconds)&&p.warmup_seconds>=15&&p.warmup_seconds<=120,'BOUNDED_WARMUP_REQUIRED');
  check(Number.isInteger(p.measurement_seconds)&&p.measurement_seconds>=60&&p.measurement_seconds<=300,'BOUNDED_DURATION_REQUIRED');
  check(typeof plan.run_id==='string'&&/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(plan.run_id),'SAFE_RUN_ID_REQUIRED');
  let origin;
  try {origin=new URL(plan.target_origin);} catch {missing.push('target_origin');}
  if(origin) {
    check(origin.protocol==='https:'&&!origin.username&&!origin.password&&!origin.search&&!origin.hash
      &&origin.pathname==='/'&&origin.port==='','HTTPS_ORIGIN_ONLY_REQUIRED');
    if(origin.hostname.endsWith('.invalid'))missing.push('actual_cloud_origin');
    check(!['localhost','127.0.0.1','0.0.0.0','::1'].includes(origin.hostname),'CLOUD_TARGET_NOT_LOOPBACK');
  }
  check(plan.release?.app_source_sha===APP_SOURCE,'PINNED_UI_CONTRACT_SOURCE_REQUIRED');
  for(const name of ['backend_digest','frontend_digest'])
    if(!/^sha256:[a-f0-9]{64}$/.test(plan.release?.[name]||''))missing.push(name);
  for(const name of ['normal_business_ref','release_binding_ref','run_window_ref'])
    if(typeof plan.evidence?.[name]!=='string'||!plan.evidence[name].trim())missing.push(name);
  for(const name of ['backend','frontend'])
    if(!Number.isInteger(plan.runtime?.replicas?.[name])||plan.runtime.replicas[name]<1)missing.push('actual_'+name+'_replicas');
  for(const name of ['config_revision','schema_revision'])
    if(typeof plan.runtime?.[name]!=='string'||!plan.runtime[name].trim())missing.push('actual_'+name);
  if(!Number.isInteger(plan.runtime?.pool_size)||plan.runtime.pool_size<1
    ||!Number.isInteger(plan.runtime?.max_overflow)||plan.runtime.max_overflow<0
    ||!Number.isFinite(plan.runtime?.pool_timeout_seconds)||plan.runtime.pool_timeout_seconds<=0)missing.push('actual_pool_tuple');
  const start=Date.parse(plan.window?.not_before||''),end=Date.parse(plan.window?.not_after||'');
  const zoned=x=>typeof x==='string'&&/(Z|[+-]\d\d:\d\d)$/.test(x);
  if(!zoned(plan.window?.not_before)||!zoned(plan.window?.not_after)||!Number.isFinite(start)||!Number.isFinite(end)
    ||now<start||now+(p.warmup_seconds+p.measurement_seconds+270)*1000>end)missing.push('current_window_with_setup_margin');
  return {status:missing.length?'PENDING_CLOUD_MEASUREMENT_INPUTS':'PASS_LOCAL_PLAN_NOT_EXECUTED',missing,
    target_origin:origin?.origin??null,network_requests:0,accounts_created:0};
}

export function targetRequestAllowed(raw, origin, method='GET') {
  try {const u=new URL(raw);return u.origin===origin&&!u.username&&!u.password
    &&(['GET','HEAD'].includes(method)||u.pathname.startsWith('/api/v1/'));}catch{return false;}
}
export function targetSocketAllowed(raw, origin) {
  try {const u=new URL(raw),o=new URL(origin);return u.protocol==='wss:'&&u.host===o.host&&!u.search&&!u.hash
    &&/^\/ws\/v1\/(?:presence|lobby|rooms\/[A-Za-z0-9_-]{1,128}|chat\/(?:lobby|rooms\/[A-Za-z0-9_-]{1,128}))$/.test(u.pathname);}catch{return false;}
}

export function parseTurn(text) {
  const n=/투표 기회\s+(\d+)번째/.exec(text);
  return n&&Number.isSafeInteger(Number(n[1]))&&Number(n[1])>0?Number(n[1]):null;
}

// Separate two-dimensional sublattices: same-team stones cannot be adjacent in
// any of the four line directions. This is not an assertion about an existing board.
export function coordinates(team) {
  check(['BLACK','WHITE'].includes(team),'KNOWN_TEAM_REQUIRED');
  const parity=team==='BLACK'?0:1,result=[];
  for(let y=parity;y<15;y+=2)for(let x=parity;x<15;x+=2)result.push(String.fromCharCode(65+x)+(y+1));
  return result;
}

async function eventually(fn, code, clock=realClock, timeout=10000) {
  const end=clock.now()+timeout;
  while(clock.now()<end){if(await fn())return;await clock.sleep(100);}
  check(false,code);
}

export async function openPanel(page,title) {
  const trigger=page.getByRole('button',{name:new RegExp(title)});
  await trigger.focus();await trigger.press('Escape');await trigger.press('Enter');
  check(await trigger.getAttribute('aria-expanded')==='true','WORKSPACE_PANEL_NOT_OPEN');
}

async function member(page,suffix,counters) {
  const login='m'+randomUUID().replaceAll('-','').slice(0,14),name='m'+suffix+login.slice(-5),password=randomUUID();
  await page.goto('/');
  await page.getByRole('button',{name:'회원가입',exact:true}).click();
  const form=page.getByRole('dialog',{name:'Member 회원가입'});
  await form.getByLabel('아이디',{exact:true}).fill(login);
  await form.getByLabel('닉네임',{exact:true}).fill(name);
  await form.getByLabel('비밀번호',{exact:true}).fill(password);
  counters.member_create_attempts++;
  await page.getByRole('button',{name:'가입하기',exact:true}).click();
  await page.getByText('회원가입이 완료되었습니다. 아이디와 비밀번호로 로그인해 주세요.',{exact:true}).waitFor();
  counters.members_confirmed++;
  await page.getByLabel('아이디',{exact:true}).fill(login);
  await page.getByLabel('비밀번호',{exact:true}).fill(password);
  await page.getByRole('button',{name:'로그인',exact:true}).click();
  await page.getByRole('heading',{name:'게임 방',exact:true}).waitFor();
  return name;
}

export class BrowserRoom {
  constructor(pages,ids,meter,counters,clock=realClock) {
    [this.black,this.white,this.guest]=pages;this.pages=pages;this.ids=ids;this.meter=meter;
    this.counters=counters;this.clock=clock;this.title='metric-'+randomUUID().slice(0,8);
    this.password=randomUUID().slice(0,16);this.lastTurn=0;this.used=new Set();this.setupComplete=false;
    this.joined=new Set();this.cleanupAttempted=false;
  }
  async setup() {
    this.names=[await member(this.black,'b',this.counters),await member(this.white,'w',this.counters)];
    await this.guest.goto('/');
    await this.guest.getByRole('button',{name:'Guest로 시작하기 →'}).click();
    await this.guest.getByRole('heading',{name:'게임 방',exact:true}).waitFor();this.counters.guests_confirmed++;
    await this.black.getByRole('button',{name:'방 생성',exact:true}).click();
    await this.black.getByLabel('방 이름',{exact:true}).fill(this.title);
    await this.black.getByLabel('공개 여부').selectOption('PRIVATE');
    await this.black.getByLabel('방 비밀번호').fill(this.password);
    await this.black.getByLabel('최소 Ready 인원').fill('2');
    await this.black.getByLabel('투표 제한 시간').selectOption('15');
    this.counters.room_create_attempts++;
    await this.black.getByRole('button',{name:'방 만들기',exact:true}).click();
    await this.black.getByRole('heading',{name:this.title,exact:true}).waitFor();this.counters.rooms_confirmed++;this.joined.add(this.black);
    for(const page of [this.white,this.guest]){
      await page.getByRole('button',{name:'목록 새로고침'}).click();
      await page.getByRole('row').filter({hasText:this.title}).getByRole('button',{name:'입장',exact:true}).click();
      await page.getByRole('dialog',{name:'비공개 방 입장'}).getByLabel('방 비밀번호').fill(this.password);
      await page.getByRole('button',{name:'입장 확인'}).click();
      await page.getByRole('heading',{name:this.title,exact:true}).waitFor();
      this.joined.add(page);
    }
    for(const page of this.pages){
      await openPanel(page,'준비 현황 · 팀 설정');
      await eventually(async()=>{
        for(const name of this.names){const row=page.getByRole('listitem').filter({hasText:name});
          if(await row.count()!==1||(await row.innerText()).includes('연결 끊김'))return false;}
        const guest=page.getByRole('listitem').filter({hasText:'Guest-'});
        return await guest.count()===1&&!(await guest.innerText()).includes('연결 끊김');
      },'ROOM_CONNECTION_ROSTER_NOT_READY',this.clock);
    }
    await this.black.getByRole('button',{name:'흑팀 선택',exact:true}).click();
    await this.white.getByRole('list',{name:'흑팀 참가자'}).getByRole('listitem').filter({hasText:this.names[0]}).waitFor();
    await this.white.getByRole('button',{name:'백팀 선택',exact:true}).click();
    await this.black.getByRole('list',{name:'백팀 참가자'}).getByRole('listitem').filter({hasText:this.names[1]}).waitFor();
    await this.black.getByRole('button',{name:'Ready',exact:true}).click();
    await this.white.getByRole('listitem').filter({hasText:this.names[0]}).filter({hasText:' · Ready'}).waitFor();
    await this.white.getByRole('button',{name:'Ready',exact:true}).click();
    await this.black.getByRole('listitem').filter({hasText:this.names[1]}).filter({hasText:' · Ready'}).waitFor();
    await this.black.getByRole('button',{name:'게임 시작',exact:true}).click();
    await this.black.getByRole('heading',{name:'● 흑팀 차례',exact:true}).waitFor();
    this.setupComplete=true;
  }
  async step() {
    check(this.setupComplete,'COHORT_SETUP_REQUIRED');
    if(await this.black.getByRole('dialog',{name:'경기 결과',exact:true}).isVisible())check(false,'GAME_ENDED_BEFORE_WINDOW_COMPLETE');
    const turn=parseTurn(await this.black.getByRole('region',{name:'현재 투표 상태'}).innerText());
    if(turn===null||turn<=this.lastTurn)return false;
    let team=null;
    if(await this.black.getByRole('heading',{name:'● 흑팀 차례',exact:true}).isVisible())team='BLACK';
    else if(await this.white.getByRole('heading',{name:'○ 백팀 차례',exact:true}).isVisible())team='WHITE';
    if(!team)return false;
    const actor=team==='BLACK'?this.black:this.white,peer=team==='BLACK'?this.white:this.black;
    if(parseTurn(await actor.getByRole('region',{name:'현재 투표 상태'}).innerText())!==turn)return false;
    let coordinate=null,button=null;
    for(const cell of coordinates(team)){
      if(this.used.has(cell))continue;
      const candidate=actor.getByRole('button',{name:new RegExp('^'+cell+' 빈 자리(?:,|$)')});
      if(await candidate.count()===1&&await candidate.getAttribute('aria-disabled')!=='true'
        &&await candidate.isEnabled()){coordinate=cell;button=candidate;break;}
    }
    if(!coordinate)return false;
    await openPanel(peer,'실시간 투표 현황');
    // Record one intent per observed turn; no automatic retry, duplicate or fault injection.
    this.lastTurn=turn;this.used.add(coordinate);
    await this.meter.measureVote({actor,peer,clientId:this.ids[team==='BLACK'?0:1],peerId:this.ids[team==='BLACK'?1:0],
      coordinate,timeoutMs:4000,send:()=>button.click(),confirmPeer:async(page,response,timeout)=>{
        const tally=response.vote_aggregation?.find(x=>x.coordinate===coordinate);
        check(Number.isSafeInteger(tally?.count)&&tally.count>=1,'COMMITTED_TALLY_REQUIRED');
        const row=page.getByRole('list',{name:'좌표별 득표 순위'}).getByRole('listitem')
          .filter({has:page.getByText(coordinate,{exact:true})});
        await row.filter({hasText:new RegExp(tally.count+'표(?:\\s|$)')}).waitFor({state:'visible',timeout});
      }});
    return true;
  }
  async leave() {
    if(this.cleanupAttempted)return {attempted:false,confirmed:0,reason:'ALREADY_ATTEMPTED_NO_RETRY'};
    this.cleanupAttempted=true;let confirmed=0,attempted=0,unconfirmed=0;
    for(const page of [this.guest,this.white,this.black]){
      try {
        if(!this.joined.has(page)&&!await page.getByRole('heading',{name:this.title,exact:true}).isVisible())continue;
        attempted++;await page.getByRole('button',{name:'방 나가기',exact:true}).click();
        await page.getByRole('heading',{name:'게임 방',exact:true}).waitFor();confirmed++;
      }catch{unconfirmed++;}
    }
    return {attempted,confirmed,unconfirmed};
  }
}

function cpuTotals() {return os.cpus().reduce((a,c)=>{for(const[k,v]of Object.entries(c.times)){a.total+=v;if(k==='idle')a.idle+=v;}return a;},{total:0,idle:0});}
export function resourceSampler() {
  let last=cpuTotals();
  return ()=>{const next=cpuTotals(),dt=next.total-last.total,idle=next.idle-last.idle;last=next;
    return {system_cpu_percent:dt>0?Math.round((1-idle/dt)*10000)/100:null,
      system_memory_used_percent:Math.round((1-os.freemem()/os.totalmem())*10000)/100,
      node_rss_mib:Math.round(process.memoryUsage().rss/10485.76)/100};};
}

export async function runCloud(plan,{launchBrowser,roomFactory,clock=realClock,sample=resourceSampler()}={}) {
  const readiness=validatePlan(plan);check(!readiness.missing.length,'READY_EXECUTION_INPUTS_REQUIRED');
  check(typeof launchBrowser==='function','BROWSER_LAUNCH_ADAPTER_REQUIRED');
  const contexts=[],rooms=[],resources=[],counters={member_create_attempts:0,members_confirmed:0,guests_confirmed:0,room_create_attempts:0,rooms_confirmed:0};
  const sockets={observed:0,closed:0,unexpected:0,room_open:0},roomFrameClients=new Set(),meter=new Measurement({runId:plan.run_id,now:clock.now});
  let browser,normalStart=null,normalEnd=null,stage='setup',failure=null,contextsClosed=false;
  const cleanup=[];let violations=0,barrierConnections=0,lastSample=-Infinity,setupTimer;
  try {
    browser=await launchBrowser();
    setupTimer=setTimeout(()=>{violations++;void browser.close().catch(()=>{});},180000);
    for(let i=0;i<plan.profile.clients;i++){
      const context=await browser.newContext({baseURL:readiness.target_origin,acceptDownloads:false,
        ignoreHTTPSErrors:false,viewport:{width:1280,height:900}});contexts.push(context);
      await context.route('**/*',async route=>{
        const req=route.request();if(!targetRequestAllowed(req.url(),readiness.target_origin,req.method())){
          violations++;await route.abort('blockedbyclient');return;}await route.continue();
      });
      const page=await context.newPage();page.setDefaultTimeout(10000);page.setDefaultNavigationTimeout(15000);
      page.on('websocket',socket=>{
        if(!targetSocketAllowed(socket.url(),readiness.target_origin)){
          sockets.unexpected++;void context.close().catch(()=>{});return;}
        const room=/\/ws\/v1\/rooms\//.test(socket.url());
        sockets.observed++;if(room)sockets.room_open++;
        if(room)socket.on('framereceived',()=>{roomFrameClients.add('client-'+(i+1));});
        socket.on('close',()=>{sockets.closed++;if(room)sockets.room_open--;});
      });
      page.on('pageerror',()=>{violations++;});meter.attach(page,'client-'+(i+1));
      if(i%3===0)rooms.push({pages:[],ids:[]});rooms.at(-1).pages.push(page);rooms.at(-1).ids.push('client-'+(i+1));
    }
    const drivers=rooms.map(r=>roomFactory?roomFactory(r.pages,r.ids,meter,counters,clock):new BrowserRoom(r.pages,r.ids,meter,counters,clock));
    rooms.splice(0,rooms.length,...drivers);
    const setupResults=await Promise.allSettled(rooms.map(async room=>{
      try{return await room.setup();}catch(e){await browser.close().catch(()=>{});throw e;}
    }));
    const setupFailure=setupResults.find(r=>r.status==='rejected');if(setupFailure)throw setupFailure.reason;
    clearTimeout(setupTimer);
    check(contexts.length===plan.profile.clients&&rooms.every(r=>r.setupComplete),'FULL_COHORT_REQUIRED');
    barrierConnections=sockets.room_open;
    check(barrierConnections===plan.profile.clients,'FULL_ROOM_SOCKET_BARRIER_REQUIRED');
    check(roomFrameClients.size===plan.profile.clients,'EACH_ROOM_CLIENT_RECEIVED_FRAME_REQUIRED');
    const phase=async(seconds)=>{
      const end=clock.now()+seconds*1000;let abort=false;
      const results=await Promise.allSettled(rooms.map(async room=>{
        try{while(clock.now()<end&&!abort){check(violations===0&&sockets.unexpected===0,'TARGET_OR_PAGE_FAILURE');
          check(Date.now()<Date.parse(plan.window.not_after),'RUN_WINDOW_EXPIRED');
          check(sockets.room_open===plan.profile.clients,'ROOM_SOCKET_COHORT_LOST');
          await room.step();if(clock.now()-lastSample>=1000){lastSample=clock.now();resources.push({phase:stage,...sample()});}
          await clock.sleep(100);}}
        catch(e){abort=true;throw e;}
      }));
      const rejected=results.find(r=>r.status==='rejected');if(rejected)throw rejected.reason;
    };
    stage='warmup';meter.setPhase('warmup');await phase(plan.profile.warmup_seconds);
    stage='normal';meter.setPhase('normal');normalStart=clock.now();await phase(plan.profile.measurement_seconds);normalEnd=clock.now();
    check(violations===0&&sockets.unexpected===0,'TARGET_OR_PAGE_FAILURE');
    stage='cleanup';meter.setPhase('warmup');
  } catch(e) {failure={stage,reason:safeFailure(e)};if(normalStart!==null)normalEnd=clock.now();}
  finally {
    clearTimeout(setupTimer);meter.setPhase('warmup');
    cleanup.push(...await Promise.all(rooms.filter(r=>typeof r.leave==='function').map(async room=>{
      try{return await room.leave();}catch{return {attempted:true,confirmed:null,error:'OWN_ROOM_LEAVE_NOT_CONFIRMED'};}
    })));
    if(browser){try{await browser.close();contextsClosed=true;}catch{contextsClosed=false;}}
  }
  const bundle=await meter.finish(),summary=summarize(bundle),duration=normalStart===null?0:Math.max(0,(normalEnd-normalStart)/1000);
  const vote=summary.business_ws;
  const complete=!failure&&bundle.status!=='INVALID'&&duration>=plan.profile.measurement_seconds
    &&vote.first_attempts>0&&contextsClosed;
  return {receipt:{status:complete?'COMPLETED_SCOPED_CLOUD_OBSERVATION_NOT_FULL_ACCEPTANCE':'INCOMPLETE_CLOUD_OBSERVATION',
    failure,run_id:plan.run_id,requested_clients:plan.profile.clients,created_contexts:contexts.length,
    rooms_setup_complete:rooms.filter(r=>r.setupComplete).length,observed_sockets:sockets,
    room_socket_connections_at_barrier:barrierConnections,
    room_clients_with_received_frame:roomFrameClients.size,
    target_reference_sha256:createHash('sha256').update(readiness.target_origin).digest('hex'),
    app_source_sha:plan.release.app_source_sha,reported_deployment:plan.runtime,
    deployment_independently_queried:false,normal_duration_seconds:duration,
    requested_normal_seconds:plan.profile.measurement_seconds,normal_vote_first_attempts:vote.first_attempts,
    successful_vote_intents_per_second:duration>0?(vote.first_attempts-vote.first_errors)/duration:null,
    synthetic_data:counters,own_room_leave_results:cleanup,browser_closed:contextsClosed,
    member_rows_deleted:false,harness_vote_retries:0,ui_cleanup_stale_retry_source_behavior:'POSSIBLE_ONCE',fault_injection:false,
    metrics_scope:'real browser UI -> matching committed vote -> independent peer WSS/UI; per-room serial intents',
    concurrent_connections_are_not_concurrent_requests:true,
    not_verified:['maximum capacity','formal 10/30/60 sustained profile','all fault cases','full Cloud acceptance','DR RTO/RPO']},
    bundle,summary,resources};
}
