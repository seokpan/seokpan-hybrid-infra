#!/usr/bin/env node
// Default is a local plan check. Only --execute can launch a browser.
import {readFileSync,writeFileSync,mkdirSync,lstatSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {validatePlan,runCloud,safeFailure,check} from './cloud-business.mjs';

export function parseArgs(args) {
  const result={execute:false};
  for(let i=0;i<args.length;i++){
    if(args[i]==='--execute'){check(!result.execute,'DUPLICATE_EXECUTE_ARGUMENT');result.execute=true;}
    else if(['--plan','--out','--channel'].includes(args[i])){
      const key=args[i].slice(2);check(!result[key]&&args[i+1]&&!args[i+1].startsWith('--'),'ONE_ARGUMENT_VALUE_REQUIRED');
      result[key]=args[++i];
    }else check(false,'UNKNOWN_ARGUMENT');
  }
  check(result.plan,'PLAN_FILE_REQUIRED');
  check(!result.execute||result.out,'NEW_OUTPUT_DIRECTORY_REQUIRED');
  check(!result.channel||['chrome','chromium'].includes(result.channel),'KNOWN_BROWSER_CHANNEL_REQUIRED');
  return result;
}

export async function main(args,dependency={}) {
  let out=null;
  try {
    const options=parseArgs(args),path=resolve(options.plan),info=lstatSync(path);
    check(info.isFile()&&!info.isSymbolicLink()&&info.size<=65536,'REGULAR_BOUNDED_PLAN_REQUIRED');
    const plan=JSON.parse(readFileSync(path,'utf8')),ready=validatePlan(plan);
    if(!options.execute){console.log(JSON.stringify({...ready,target_origin:undefined,execution_requested:false}));return ready.missing.length?2:0;}
    check(!ready.missing.length,'CURRENT_EXECUTION_INPUTS_REQUIRED');
    check(!process.env.PWDEBUG&&!process.env.DEBUG,'DEBUG_TRACE_ENVIRONMENT_MUST_BE_UNSET');
    // Preserve old results: output must be new, without recursive replacement.
    out=resolve(options.out);mkdirSync(out,{mode:0o700,recursive:false});
    const {chromium}=dependency.playwright||await import('playwright');
    const launchBrowser=()=>chromium.launch({headless:true,...(options.channel==='chrome'?{channel:'chrome'}:{})});
    const result=await runCloud(plan,{launchBrowser,...dependency.runtime});
    const files={'observations.json':result.bundle,'summary.json':result.summary,
      'run-receipt.json':result.receipt,'generator-resources.json':result.resources};
    const hashes={};
    for(const[name,value]of Object.entries(files)){
      const text=JSON.stringify(value,null,2)+'\n';writeFileSync(join(out,name),text,{flag:'wx',mode:0o600});
      hashes[name]=createHash('sha256').update(text).digest('hex');
    }
    writeFileSync(join(out,'SHA256SUMS.json'),JSON.stringify(hashes,null,2)+'\n',{flag:'wx',mode:0o600});
    console.log(JSON.stringify(result.receipt));
    return result.receipt.status.startsWith('COMPLETED_')?0:2;
  }catch(e){
    const safe={status:'STOP_CLOUD_MEASUREMENT',reason:safeFailure(e),raw_errors_printed:false,
      create_or_restore_invoked:false,automatic_retry:false};
    console.log(JSON.stringify(safe));return 2;
  }
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url))process.exitCode=await main(process.argv.slice(2));
