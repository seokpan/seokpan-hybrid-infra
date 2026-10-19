// Local measurement candidate. No network calls, load generation or credential access.
import { createHmac, randomBytes } from "node:crypto";
import { performance } from "node:perf_hooks";

const VOTE = /^\/api\/v1\/games\/([^/]+)\/turns\/([1-9][0-9]*)\/vote$/;
const ROUTES = [
  [/^\/api\/v1\/games\/[^/]+\/turns\/[1-9][0-9]*\/vote$/, "/api/v1/games/{game_id}/turns/{turn_no}/vote"],
  [/^\/api\/v1\/games\/[^/]+\/result$/, "/api/v1/games/{game_id}/result"],
  [/^\/api\/v1\/games\/[^/]+$/, "/api/v1/games/{game_id}"],
  [/^\/api\/v1\/rooms\/[^/]+\/participants\/me\/(team|ready)$/, "/api/v1/rooms/{room_id}/participants/me/{team_or_ready}"],
  [/^\/api\/v1\/rooms\/[^/]+\/games$/, "/api/v1/rooms/{room_id}/games"],
  [/^\/api\/v1\/rooms\/[^/]+\/state$/, "/api/v1/rooms/{room_id}/state"],
  [/^\/api\/v1\/rooms\/[^/]+$/, "/api/v1/rooms/{room_id}"],
  [/^\/api\/v1\/rooms$/, "/api/v1/rooms"],
  [/^\/api\/v1\/lobby\/snapshot$/, "/api/v1/lobby/snapshot"],
];
function pathOf(url) { try { return new URL(url).pathname; } catch { return ""; } }
function slug(value) { if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(value)) throw new Error("SAFE_ID_REQUIRED"); return value; }
function positive(v) { return Number.isSafeInteger(v) && v >= 1; }
export function percentile95(values) {
  if (!values.length) return null;
  return [...values].sort((a,b) => a-b)[Math.ceil(values.length * .95)-1];
}

export class Measurement {
  constructor({ runId, now = () => performance.now(), utc = () => new Date().toISOString(), maxRows = 200000 }) {
    this.runId = slug(runId); this.now = now; this.utc = utc; this.maxRows = maxRows;
    this.key = randomBytes(32); this.rows = []; this.phase = "warmup"; this.serial = 0;
    this.http = new Map(); this.pending = new Set(); this.listeners = [];
    this.inflight = new Set(); this.actions = new Set(); this.events = []; this.duplicates = new Set(); this.invalid = false;
    this.eventCacheEvictions = 0;
  }
  pseudonym(value) { return createHmac("sha256", this.key).update(String(value)).digest("hex").slice(0,24); }
  emit(row) {
    if (this.rows.length >= this.maxRows) { this.invalid = true; throw new Error("MEASUREMENT_LIMIT_NO_SILENT_DROP"); }
    this.rows.push({run_id:this.runId,phase:this.phase,utc:this.utc(),...row});
  }
  setPhase(phase) {
    if (!['warmup','normal','fault','recovery'].includes(phase)) throw new Error("PHASE_REQUIRED");
    this.phase = phase; this.emit({kind:'phase',value:phase,monotonic_ms:this.now()});
  }
  mark(kind, evidenceRef, {scenarioId,clientId = null}) {
    if (!['fault_started','dependency_ready','client_converged','fault_ended'].includes(kind)) throw new Error("MARKER_REQUIRED");
    this.emit({kind,evidence_ref:slug(evidenceRef),scenario_id:slug(scenarioId),
      client_id:clientId===null?null:slug(clientId),monotonic_ms:this.now()});
  }
  attach(page, clientId) {
    slug(clientId);
    const on = (event, listener) => { page.on(event,listener); this.listeners.push(() => page.off(event,listener)); };
    on('request', request => {
      const route = ROUTES.find(([re]) => re.test(pathOf(request.url())))?.[1];
      if (route) this.http.set(request,{client_id:clientId,route,method:request.method(),start:this.now(),phase:this.phase});
    });
    const finish = (request, failed) => {
      const item = this.http.get(request); if (!item) return;
      this.http.delete(request);
      const ended = this.now();
      const task = (async () => {
        let status = null; let duration = null;
        if (!failed) {
          const response = await request.response(); status = response?.status() ?? null;
          const timing = request.timing();
          if (Number.isFinite(timing.responseEnd) && timing.responseEnd >= 0) duration = timing.responseEnd;
        }
        this.emit({kind:'http',...item,start:undefined,phase:item.phase,status,
          outcome:failed || status===null?'unknown':status >= 200 && status < 300?'success':'http_error',
          duration_ms:duration,observer_duration_ms:ended-item.start,
          boundary:'browser_request_start_to_response_end; success does not prove business correctness'});
      })().catch(() => { this.invalid = true; }).finally(() => this.pending.delete(task));
      this.pending.add(task);
    };
    on('requestfinished', r => finish(r,false));
    on('requestfailed', r => finish(r,true));
    on('websocket', socket => {
      if (!/^\/ws\/v1\/rooms\/[^/]+$/.test(pathOf(socket.url()))) return;
      const receive = ({payload}) => {
        if (typeof payload !== 'string' || Buffer.byteLength(payload) > 131072) return;
        let event; try { event = JSON.parse(payload); } catch { return; }
        if (event.schema_version !== 1 || event.event_type !== 'vote.tally_changed' || !positive(event.turn_no) ||
            !positive(event.payload?.game_state_version) || typeof event.game_id !== 'string' || typeof event.event_id !== 'string') return;
        const dedup = clientId+':'+this.pseudonym(event.event_id);
        if (this.duplicates.has(dedup)) return;
        if (this.duplicates.size >= 200000) { this.invalid=true; throw new Error('EVENT_LIMIT_NO_SILENT_DROP'); }
        this.duplicates.add(dedup);
        const safe = {client_id:clientId, game:this.pseudonym(event.game_id), turn:event.turn_no,
          game_version:event.payload.game_state_version, at:this.now(), event:this.pseudonym(event.event_id)};
        // Only stripped correlation metadata remains; never retain the raw frame/payload.
        this.events.push(safe);
        if (this.events.length > 5000) { this.events.shift(); this.eventCacheEvictions++; }
      };
      socket.on('framereceived',receive); this.listeners.push(() => socket.off('framereceived',receive));
      const closed = () => this.emit({kind:'ws_connection_closed',client_id:clientId,monotonic_ms:this.now()});
      socket.on('close',closed); this.listeners.push(() => socket.off('close',closed));
    });
  }
  async measureVote({ actor, peer, clientId, peerId, coordinate, actionId, retryOf = null,
                      expectedNegative = false, timeoutMs = 15000, send, confirmPeer }) {
    slug(clientId); slug(peerId); actionId = slug(actionId ?? `action-${++this.serial}`);
    if (retryOf !== null) slug(retryOf);
    if (this.actions.has(actionId)) throw new Error('UNIQUE_ACTION_ID_REQUIRED');
    if (this.inflight.has(clientId)) throw new Error('SERIAL_ACTION_PER_CLIENT_REQUIRED');
    if (clientId===peerId || actor===peer) throw new Error('INDEPENDENT_PEER_REQUIRED');
    if (!Number.isFinite(timeoutMs) || timeoutMs<=0) throw new Error('POSITIVE_TIMEOUT_REQUIRED');
    if (!/^[A-S](?:[1-9]|1[0-9])$/.test(coordinate)) throw new Error('COORDINATE_REQUIRED');
    this.inflight.add(clientId); this.actions.add(actionId);
    const started = this.now(), phase = this.phase;
    let matchedRequest = null, requestId = null, expected = null, game = null, turn = null;
    let httpMs = null, event = null, outcome = 'unknown', accepted = false;
    const capture = request => {
      if (request.method() !== 'PUT') return;
      const match = VOTE.exec(pathOf(request.url())); if (!match) return;
      let body; try { body = request.postDataJSON(); } catch { return; }
      if (body?.coordinate !== coordinate || !positive(body.expected_state_version) ||
          typeof body.request_id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(body.request_id)) return;
      if (matchedRequest) { this.invalid = true; return; }
      matchedRequest = request; requestId = this.pseudonym(body.request_id);
      expected = body.expected_state_version; game = match[1]; turn = Number(match[2]);
      const observation = this.http.get(request);
      if (observation) Object.assign(observation,{action_id:actionId,retry_of:retryOf,expected_negative:expectedNegative});
    };
    actor.on('request',capture);
    try {
      // Install the wait before send so a fast HTTP response or WS event is not missed.
      const responseWait = actor.waitForResponse(r => r.request() === matchedRequest, {timeout:timeoutMs});
      responseWait.catch(() => {});
      await send();
      const response = await responseWait;
      if (response.status() < 200 || response.status() >= 300) { outcome='http_error'; return; }
      const value = await response.json();
      httpMs = this.now()-started;
      if (!matchedRequest || value.game_id !== game || value.turn_no !== turn || value.my_vote !== coordinate ||
          !positive(value.state_version) || value.state_version <= expected || value.replayed === true) {
        outcome='unconfirmed'; return;
      }
      accepted = true;
      const gameHash = this.pseudonym(game);
      const deadline = started + timeoutMs;
      while (this.now() < deadline) {
        event = this.events.find(e => e.client_id === peerId && e.game === gameHash && e.turn === turn &&
          e.game_version === value.state_version && e.at >= started);
        if (event) break;
        await new Promise(resolve => setTimeout(resolve,5));
      }
      if (!event) return;
      // A separate peer's UI is required: sender my_vote can change from HTTP alone.
      const remaining = Math.max(1,deadline-this.now());
      await confirmPeer(peer,value,remaining);
      if (this.now() > deadline) return;
      outcome='success';
    } catch { outcome='unknown'; } // Never export raw Playwright/HTTP errors or screenshots.
    finally {
      actor.off('request',capture); this.inflight.delete(clientId);
      this.emit({kind:'business_ws',phase,action_id:actionId,retry_of:retryOf,client_id:clientId,peer_id:peerId,
        operation:'vote_cast',expected_negative:expectedNegative,request_ref:requestId,game_ref:game===null?null:this.pseudonym(game),
        turn_no:turn,accepted_http:accepted,outcome,http_command_to_validated_response_ms:httpMs,
        command_to_ws_receive_ms:event ? event.at-started : null,
        duration_ms:outcome==='success'?this.now()-started:null,
        boundary:'action_start -> matching committed game version on peer room WS -> peer UI tally confirmation'});
    }
  }
  async finish() {
    this.listeners.splice(0).forEach(fn => fn());
    await Promise.all([...this.pending]);
    for (const item of this.http.values()) this.emit({kind:'http',client_id:item.client_id,route:item.route,method:item.method,
      phase:item.phase,status:null,outcome:'unknown',duration_ms:null,observer_duration_ms:this.now()-item.start,boundary:'unfinished_at_collection_end'});
    this.http.clear();
    if (this.inflight.size) this.invalid=true;
    return {status:this.invalid?'INVALID':'MEASUREMENT_SOURCE_ONLY',event_cache_evictions:this.eventCacheEvictions,rows:this.rows};
  }
}

export function summarize(bundle) {
  const rows = bundle.rows; const normal = rows.filter(r => r.phase==='normal');
  const byRoute = {};
  for (const row of normal.filter(r => r.kind==='http' && !r.expected_negative)) {
    const key = row.method+' '+row.route;
    (byRoute[key] ??= []).push(row);
  }
  const http = Object.fromEntries(Object.entries(byRoute).map(([key,items]) => [key,{
    attempts:items.length,errors:items.filter(r => r.outcome!=='success').length,
    success_p95_ms:percentile95(items.filter(r => r.outcome==='success' && r.duration_ms!==null).map(r => r.duration_ms)),
    unmeasured_successes:items.filter(r => r.outcome==='success' && r.duration_ms===null).length,
  }]));
  const business = normal.filter(r => r.kind==='business_ws' && !r.expected_negative);
  const first = business.filter(r => r.retry_of===null);
  const groups = new Map();
  for (const r of business) {
    const root = r.retry_of ?? r.action_id;
    const a = groups.get(root) ?? []; a.push(r); groups.set(root,a);
  }
  const orphanRetries = business.filter(r => r.retry_of!==null && !first.some(f => f.action_id===r.retry_of)).length;
  const firstErrors = first.filter(r => r.outcome!=='success').length;
  const markers = rows.filter(r => ['fault_started','dependency_ready','client_converged'].includes(r.kind));
  const recovery = markers.filter(r => r.kind==='client_converged').map(r => {
    const fault = markers.filter(x => x.kind==='fault_started' && x.scenario_id===r.scenario_id && x.monotonic_ms<=r.monotonic_ms).at(-1);
    const ready = markers.filter(x => x.kind==='dependency_ready' && x.scenario_id===r.scenario_id &&
      fault && x.monotonic_ms>=fault.monotonic_ms && x.monotonic_ms<=r.monotonic_ms).at(-1);
    return {scenario_id:r.scenario_id,client_id:r.client_id,ready_to_converged_ms:ready?r.monotonic_ms-ready.monotonic_ms:null,
      fault_to_converged_ms:fault?r.monotonic_ms-fault.monotonic_ms:null,evidence_ref:r.evidence_ref};
  });
  return {status:'NOT_RUNTIME_ACCEPTANCE',source_status:bundle.status,http,
    business_ws:{first_attempts:first.length,first_errors:firstErrors,first_error_rate:first.length?firstErrors/first.length:null,
      retry_attempts:business.length-first.length,orphan_retries:orphanRetries,
      final_failures:[...groups.values()].filter(a=>a.at(-1).outcome!=='success').length,
      success_first_attempt_p95_ms:percentile95(first.filter(r=>r.outcome==='success').map(r=>r.duration_ms)),
      success_all_attempt_p95_ms:percentile95(business.filter(r=>r.outcome==='success').map(r=>r.duration_ms))},recovery,
    not_verified:['target concurrency/duration and generator capacity','real provider/network/route','operating window and resource reserve',
      'business correctness across full workload','metric/log/alert collection and clock correspondence'],
    measurement_scope:{business_ws:'vote_cast only; peer WS + peer UI',
      http:'allowlisted browser HTTP observations; expected negatives excluded when tagged; unwrapped intent/retry classification is unknown',
      first_attempt_error_rate:'instrumented normal vote intents only; unknown/unconfirmed included; not an overall workload verdict',
      cache_evictions:bundle.event_cache_evictions??0}};
}
