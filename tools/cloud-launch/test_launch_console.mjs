// New generated-console contract tests only, using an in-process fetch mock.
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';
const template = readFileSync(new URL('./compare-and-sync.js', import.meta.url), 'utf8');
const hash = async value => Buffer.from(await webcrypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(value)))).toString('hex');
const sorted = x => Array.isArray(x) ? x.map(sorted) : x && typeof x === 'object'
  ? Object.fromEntries(Object.keys(x).sort().map(k => [k, sorted(x[k])])) : x;
async function run({sync=false, changed=false, expired=false, confirm=true, postFails=false}={}) {
  const object = {apiVersion:'v1',kind:'ConfigMap',metadata:{name:'backend-config-synthetic',namespace:'seokpan-cloud'},data:{synthetic:'fixture'}};
  const source={repoURL:'https://github.com/seokpan/seokpan-hybrid-gitops.git',targetRevision:'a'.repeat(40),path:'apps/overlays/cloud/runtime'};
  const baseline={origin:'https://argo.example.test',application_uid:'fixture-app',revision:'a'.repeat(40),source,
    objects:{'v1|ConfigMap|seokpan-cloud|backend-config-synthetic':await hash(sorted(object))},stage:'single',
    not_before:'2020-01-01T00:00:00Z',not_after:expired?'2021-01-01T00:00:00Z':'2099-01-01T00:00:00Z'};
  const application={metadata:{name:'seokpan-cloud-app',namespace:'openshift-gitops',uid:'fixture-app',resourceVersion:'1',
    annotations:{'seokpan.io/bootstrap-owner':'cloud-app-held'}},spec:{project:'seokpan-cloud-app',source,
    destination:{namespace:'seokpan-cloud',server:'https://kubernetes.default.svc'},
    syncPolicy:{syncOptions:['Prune=false','Delete=false','FailOnSharedResource=true']}},status:{}};
  const calls=[],outputs=[],storage=new Map();
  const context={location:{origin:baseline.origin},crypto:webcrypto,TextEncoder,structuredClone,Date,JSON,Object,Array,Error,
    confirm:()=>confirm,sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},
    console:{log:x=>outputs.push(JSON.parse(x)),error:x=>outputs.push(JSON.parse(x))},
    fetch:async(path,options)=>{
      calls.push({path,options});
      if(options.method==='POST'){
        const body=JSON.parse(options.body);assert.equal(body.prune,false);assert.equal(body.dryRun,false);
        assert.equal(body.retryStrategy.limit,0);
        assert.equal(body.resources.length,1);assert.equal(body.resources[0].namespace,'seokpan-cloud');
        return {ok:!postFails,status:postFails?504:200,json:async()=>application};
      }
      const modified=structuredClone(object);if(changed)modified.data.synthetic='different';
      return {ok:true,json:async()=>path.includes('/manifests')?{manifests:[JSON.stringify(modified)]}:application};
    }};
  const code=template.replace('__LAUNCH_BASELINE__',JSON.stringify(baseline)).replace('const SYNC_NOW = false','const SYNC_NOW = '+sync);
  await vm.runInNewContext(code,context);
  return {calls,outputs,storage};
}
test('default compares only and never starts workload sync',async()=>{const r=await run();assert.equal(r.calls.filter(x=>x.options.method==='POST').length,0);assert.equal(r.outputs[0].status,'PASS_NEW_STAGE_SERVER_MANIFESTS_NO_SYNC');});
test('a changed desired object blocks sync',async()=>{const r=await run({sync:true,changed:true});assert.equal(r.outputs[0].reason,'SERVER_DESIRED_MANIFEST_DIFFERENCE');assert.equal(r.calls.filter(x=>x.options.method==='POST').length,0);});
test('explicit sync uses only the compared resource set',async()=>{const r=await run({sync:true});assert.equal(r.calls.filter(x=>x.options.method==='POST').length,1);assert.equal(r.outputs[0].status,'SUBMITTED_CLOUD_STAGE_SYNC_NOT_RUNTIME_ACCEPTANCE');});
test('declined confirmation has no POST',async()=>{const r=await run({sync:true,confirm:false});assert.equal(r.outputs[0].reason,'SYNC_NOT_CONFIRMED');assert.equal(r.calls.filter(x=>x.options.method==='POST').length,0);});
test('expired window stops before requests',async()=>{const r=await run({expired:true});assert.equal(r.calls.length,0);});
test('unknown sync outcome is marked and never retried',async()=>{const r=await run({sync:true,postFails:true});assert.equal(r.calls.filter(x=>x.options.method==='POST').length,1);assert.equal(r.storage.size,1);assert.equal(r.outputs[0].reason,'ARGO_HTTP_504');});
