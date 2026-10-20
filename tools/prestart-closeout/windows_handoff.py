"""Use the finished restricted SSH route. No recreation and no default browser run."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

def need(ok,code):
    if not ok:raise RuntimeError(code)
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def configuration(settings):
    c=read(settings)
    need(c.get('schema')==1,'EXPLICIT_WINDOWS_SETTINGS_REQUIRED')
    for name in ('execution_paths_file','measurement_source','result_root','controller_alias'):
        need(isinstance(c.get(name),str) and c[name] and 'REPLACE_' not in c[name],
             'EXPLICIT_WINDOWS_PATHS_REQUIRED')
    paths=read(c['execution_paths_file'])
    prefix=paths.get('controller_trigger_prefix')
    need(isinstance(prefix,list) and all(isinstance(x,str) and x for x in prefix)
         and len(prefix)>1 and prefix[-1]==c['controller_alias'],'EXISTING_ACCEPTED_SSH_ROUTE_REQUIRED')
    return paths,prefix,Path(c['measurement_source']),Path(c['result_root'])
def save(path,value):
    data=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
    if path.exists():need(path.read_bytes()==data,'EXISTING_DIFFERENT_RESULT_PRESERVED');return
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['report','fetch-browser','external','baseline','rolling'])
    p.add_argument('--settings',required=True,type=Path)
    p.add_argument('--stage',choices=['single','shared','target'],default='target');p.add_argument('--execute',action='store_true');a=p.parse_args()
    try:
        c,prefix,source,out=configuration(a.settings)
        def remote(command,value=None):
            r=subprocess.run(prefix+[command],input=None if value is None else json.dumps(value).encode(),capture_output=True,
                timeout=20,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            need(r.returncode==0,'RESTRICTED_METADATA_ROUTE_NOT_INSTALLED_OR_UNAVAILABLE_NO_RETRY')
            need(len(r.stdout)<1048576,'BOUNDED_METADATA_OUTPUT_REQUIRED');return json.loads(r.stdout)
        if a.action=='report':print(json.dumps(remote('b-prestart-report-v1'),indent=2));return 0
        out.mkdir(exist_ok=True)
        if a.action=='external':
            need(a.execute,'EXPLICIT_EXTERNAL_BROWSER_EXECUTION_REQUIRED')
            request=remote('b-stage-request-v1 '+a.stage)
            need(request['binding']['stage']==a.stage,'EXACT_STAGE_REQUEST_REQUIRED')
            folder=out/(request['binding']['run_id']+'-'+a.stage+'-external')
            path=out/(request['binding']['run_id']+'-'+a.stage+'-request.json');save(path,request)
            args=[c['observer_node'],str(source/'run-observer.mjs'),'--request',str(path),
                '--playwright-module',c['observer_playwright_module'],'--out',str(folder),'--execute']
            r=subprocess.run(args,timeout=300,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            need(r.returncode==0,'EXTERNAL_CHECK_STOPPED_PRESERVE_EXISTING_OUTPUT')
            print(json.dumps(remote('b-stage-result-v1 '+a.stage,read(folder/'external-result.json'))));return 0
        profiles=remote('b-browser-inputs-v1')
        run_id=profiles['baseline.json']['run_id'];folder=out/run_id;folder.mkdir(exist_ok=True)
        for name,value in profiles.items():save(folder/name,value)
        if a.action=='fetch-browser':print(json.dumps({'status':'FETCHED_BOUND_BROWSER_INPUTS_NOT_EXECUTED','directory':str(folder)}));return 0
        need(a.execute,'EXPLICIT_MEASUREMENT_BROWSER_EXECUTION_REQUIRED')
        args=[c['observer_node'],str(source/'run-observer.mjs'),'--plan',str(folder/(a.action+'.json')),
            '--playwright-module',c['observer_playwright_module'],'--out',str(folder/(a.action+'-result')),
            '--abort-file',str(folder/(a.action+'.abort')),'--execute']
        if a.action=='rolling':args+=['--trigger',str(folder/'trigger.json')]
        return subprocess.run(args,timeout=900,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0).returncode
    except Exception as e:
        print(json.dumps({'status':'STOP_WINDOWS_HANDOFF','reason':str(e) if type(e) is RuntimeError else type(e).__name__,
            'recreation_started':False,'automatic_retry':False}));return 2

if __name__=='__main__':raise SystemExit(main())
