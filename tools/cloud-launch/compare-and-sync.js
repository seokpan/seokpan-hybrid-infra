/* Generated private baseline. Default is GET-only; no Secret values or tokens. */
(async () => {
  const b = __LAUNCH_BASELINE__;
  const SYNC_NOW = false; // Change to true only when this stage is ready to start.
  const APP = 'seokpan-cloud-app', NS = 'seokpan-cloud';
  const check = (ok, code) => { if (!ok) { const e = Error(code); e.name = 'LaunchStop'; throw e; } };
  check(location.origin === b.origin, 'EXPECTED_NEW_ARGO_ORIGIN');
  const inWindow = () => Date.now() >= Date.parse(b.not_before) && Date.now() < Date.parse(b.not_after);
  check(inWindow(), 'OUTSIDE_EXECUTION_WINDOW');
  const sorted = x => Array.isArray(x) ? x.map(sorted) : x && typeof x === 'object'
    ? Object.fromEntries(Object.keys(x).sort().map(k => [k, sorted(x[k])])) : x;
  const hash = async x => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',
    new TextEncoder().encode(JSON.stringify(sorted(x)))))).map(n => n.toString(16).padStart(2, '0')).join('');
  const request = async (path, options = {}) => {
    const response = await fetch(path, {method: 'GET', credentials: 'same-origin', redirect: 'error', ...options});
    check(response.ok, 'ARGO_HTTP_' + response.status); return response.json();
  };
  const root = '/api/v1/applications/' + APP, query = '?appNamespace=openshift-gitops';
  const normalizeSource = raw => {
    const s = structuredClone(raw);
    for (const p of s.kustomize?.patches || []) {
      if (p.target?.group === '') delete p.target.group;
      if (typeof p.patch === 'string') p.patch = JSON.parse(p.patch);
    }
    return s;
  };
  const inspect = async a => {
    const m = a.metadata || {}, s = a.spec || {};
    check(m.uid === b.application_uid && m.name === APP && m.namespace === 'openshift-gitops' &&
      m.annotations?.['seokpan.io/bootstrap-owner'] === 'cloud-app-held' && !m.deletionTimestamp &&
      !(m.ownerReferences || []).length && !(m.finalizers || []).length, 'APP_IDENTITY_OR_OWNER_CHANGED');
    check(s.project === APP && s.destination?.namespace === NS && s.destination?.server === 'https://kubernetes.default.svc' &&
      !s.sources && !s.syncPolicy?.automated && !a.operation &&
      !['Running', 'Terminating'].includes(a.status?.operationState?.phase) && !(a.status?.conditions || []).length, 'MANUAL_IDLE_APP_REQUIRED');
    check(['Prune=false','Delete=false','FailOnSharedResource=true'].every(x => (s.syncPolicy?.syncOptions || []).includes(x)), 'SYNC_PROTECTION_CHANGED');
    check(await hash(normalizeSource(s.source)) === await hash(b.source), 'STAGED_SOURCE_CHANGED');
    return m.resourceVersion;
  };
  await inspect(await request(root + query));
  const response = await request(root + '/manifests' + query + '&revision=' + b.revision);
  check(Array.isArray(response.manifests) && response.manifests.length === Object.keys(b.objects).length, 'MANIFEST_COUNT');
  const actual = {}, resources = [];
  for (const raw of response.manifests) {
    const o = JSON.parse(raw), m = o.metadata;
    check(m?.namespace === NS && typeof m.name === 'string', 'OBJECT_TARGET');
    check(o.status == null || (typeof o.status === 'object' && !Array.isArray(o.status) && !Object.keys(o.status).length), 'DESIRED_STATUS');
    delete o.status; check(m.creationTimestamp == null, 'DESIRED_TIMESTAMP'); delete m.creationTimestamp;
    for (const key of ['app.kubernetes.io/instance', 'argocd.argoproj.io/instance']) {
      if (Object.hasOwn(m.labels || {}, key)) { check(m.labels[key] === APP, 'TRACKING_OWNER'); delete m.labels[key]; }
    }
    const group = o.apiVersion.includes('/') ? o.apiVersion.split('/')[0] : '';
    const key = 'argocd.argoproj.io/tracking-id';
    if (Object.hasOwn(m.annotations || {}, key)) {
      check(m.annotations[key] === APP + ':' + group + '/' + o.kind + ':' + NS + '/' + m.name, 'TRACKING_OWNER'); delete m.annotations[key];
    }
    for (const key of ['labels', 'annotations']) if (m[key] && !Object.keys(m[key]).length) delete m[key];
    const id = [o.apiVersion, o.kind, NS, m.name].join('|'); check(!Object.hasOwn(actual, id), 'DUPLICATE_OBJECT');
    actual[id] = await hash(o); resources.push({group, kind: o.kind, namespace: NS, name: m.name});
  }
  check(await hash(actual) === await hash(b.objects), 'SERVER_DESIRED_MANIFEST_DIFFERENCE');
  await inspect(await request(root + query));
  let started = false;
  if (SYNC_NOW) {
    check(confirm('Start Cloud stage ' + b.stage + ' on this new cluster? No prune.'), 'SYNC_NOT_CONFIRMED');
    check(inWindow(), 'OUTSIDE_EXECUTION_WINDOW');
    await inspect(await request(root + query));
    const marker = 'seokpan-launch-' + b.application_uid + '-' + b.stage + '-' + await hash(b.source);
    check(!sessionStorage.getItem(marker), 'PRIOR_SYNC_SUBMISSION_REQUIRES_RECONCILIATION');
    sessionStorage.setItem(marker, 'SUBMISSION_ATTEMPTED_NO_AUTOMATIC_RETRY');
    await request(root + '/sync' + query, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: APP, appNamespace: 'openshift-gitops', revision: b.revision,
        prune: false, dryRun: false, resources, retryStrategy: {limit: 0},
        syncOptions: {items: ['Prune=false', 'Delete=false', 'FailOnSharedResource=true']}})});
    started = true;
  }
  console.log(JSON.stringify({status: started ? 'SUBMITTED_CLOUD_STAGE_SYNC_NOT_RUNTIME_ACCEPTANCE' : 'PASS_NEW_STAGE_SERVER_MANIFESTS_NO_SYNC',
    stage: b.stage, application_uid: b.application_uid, revision: b.revision,
    objects: Object.keys(actual).length, sync_started: started, prune: false, sql_calls: 0, automatic_retry: false}, null, 2));
})().catch(e => console.error(JSON.stringify({status: 'STOP_CLOUD_STAGE_CONSOLE',
  reason: e.name === 'LaunchStop' ? e.message : e.name, automatic_retry: false, raw_response_printed: false})));
