#!/usr/bin/env node
// Default validates local inputs only. Actual browser/trigger execution is opt-in.
import {readFileSync,writeFileSync,mkdirSync,existsSync,lstatSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {runFault,externalCheck,validateFaultPlan,validateTriggerPlan} from './fault-observer.mjs';
import {validatePlan,check,safeFailure} from './vendor/cloud-business.mjs';

const options={};
try{
  for(let i=2;i<process.argv.length;i++){
    const v=process.argv[i];if(v==='--execute'){check(!options.execute,'DUPLICATE_EXECUTE');options.execute=true;}
    else{check(['--plan','--request','--out','--playwright-module','--trigger','--abort-file'].includes(v)&&process.argv[i+1]&&!options[v], 'KNOWN_UNIQUE_ARGUMENT_REQUIRED');options[v]=process.argv[++i];}
  }
  const load=p=>{const s=lstatSync(p);check(s.isFile()&&!s.isSymbolicLink()&&s.size<65536,'BOUNDED_REGULAR_JSON_REQUIRED');return JSON.parse(readFileSync(p,'utf8'));};
  check(Boolean(options['--plan'])!==Boolean(options['--request']),'ONE_PLAN_OR_EXTERNAL_REQUEST_REQUIRED');
  const external=!!options['--request'],plan=load(options['--request']||options['--plan']);
  if(!external)validateFaultPlan(plan);
  check(external||plan.scenario!=='rolling'||options['--trigger'],'BOUND_TRIGGER_FILE_REQUIRED_BEFORE_BROWSER');
  const triggerInput=!external&&plan.scenario==='rolling'
    ?validateTriggerPlan(load(options['--trigger']),plan):null;
  if(!options.execute){console.log(JSON.stringify({status:'LOCAL_OBSERVER_INPUTS_ONLY_NOT_EXECUTED',external,browser_started:false}));}
  else{
    check(options['--out']&&options['--playwright-module'],'NEW_OUTPUT_AND_EXISTING_PLAYWRIGHT_MODULE_REQUIRED');
    check(!process.env.DEBUG&&!process.env.PWDEBUG,'DEBUG_TRACE_MUST_BE_UNSET');
    const out=resolve(options['--out']);mkdirSync(out,{mode:0o700});
    const playwright=await import(pathToFileURL(resolve(options['--playwright-module'])).href);
    const launchBrowser=()=>playwright.chromium.launch({channel:'chrome',headless:true});
    let attempted=false;
    const trigger=async()=>{
      check(!attempted&&options['--trigger'],'ONE_EXPLICIT_ROLLING_COMMAND_REQUIRED');attempted=true;
      const value=triggerInput;
      // The command is supplied by the designated single operator. It must call
      // rolling_control trigger with the bound plan. No shell interpolation.
      writeFileSync(join(out,'trigger.STARTED.json'),JSON.stringify({argv_sha256:createHash('sha256').update(JSON.stringify(value.argv)).digest('hex'),utc:new Date().toISOString()}),{flag:'wx',mode:0o600});
      return new Promise((res,rej)=>{
        const child=spawn(value.argv[0],value.argv.slice(1),{shell:false,stdio:['ignore','pipe','ignore'],windowsHide:true});
        let stdout='';const timer=setTimeout(()=>{child.kill();rej(Error('TRIGGER_RESPONSE_UNKNOWN_RECONCILE_NO_RETRY'));},30000);
        child.stdout.on('data',x=>{stdout+=x;if(stdout.length>65536){child.kill();}});
        child.on('error',()=>{clearTimeout(timer);rej(Error('TRIGGER_COMMAND_FAILED'));});
        child.on('exit',code=>{clearTimeout(timer);try{
          const lines=stdout.trim().split('\n'),r=JSON.parse(lines.at(-1));
          check(code===0&&r.status==='REQUESTED_ONE_GITOPS_BACKEND_ROLL'
            &&r.application_uid===plan.binding.application_uid&&r.namespace_uid===plan.binding.namespace_uid,'TRIGGER_NOT_CONFIRMED');res(r);
        }catch{rej(Error('TRIGGER_RESPONSE_UNKNOWN_RECONCILE_NO_RETRY'));}});
      });
    };
    if(!external&&plan.scenario==='rolling')check(options['--trigger'],'PREBOUND_TRIGGER_PATH_REQUIRED_BEFORE_BROWSER');
    const r=external?await externalCheck(plan,{launchBrowser}):await runFault(plan,{launchBrowser,trigger,abortRequested:()=>!!options['--abort-file']&&existsSync(options['--abort-file'])});
    const files=external?{'external-result.json':r}:{'receipt.json':r.receipt,'counts.json':r.counts,'raw-observations.json':r.bundle,
      'room-connections.json':r.connections,'generator-resources.json':r.resources};
    const hashes={};for(const [name,value]of Object.entries(files)){
      const text=JSON.stringify(value,null,2)+'\n';writeFileSync(join(out,name),text,{flag:'wx',mode:0o600});hashes[name]=createHash('sha256').update(text).digest('hex');}
    writeFileSync(join(out,'SHA256SUMS.json'),JSON.stringify(hashes,null,2)+'\n',{flag:'wx',mode:0o600});
    console.log(JSON.stringify(external?r:r.receipt));
    if(!external&&r.receipt.status.startsWith('INCOMPLETE'))process.exitCode=2;
  }
}catch(e){console.log(JSON.stringify({status:'STOP_PRESENTATION_OBSERVER',reason:safeFailure(e),automatic_retry:false,raw_error_exported:false}));process.exitCode=2;}
