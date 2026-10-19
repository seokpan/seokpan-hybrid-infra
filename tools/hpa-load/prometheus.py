"""Optional existing personal-token metrics collector. No Secret get, token argument or raw errors."""
import argparse
from pathlib import Path
import ssl
import sys
import urllib.request
import urllib.parse
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import *

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def result(value):
    need(value.get('status')=='success' and isinstance(value.get('data',{}).get('result'),list),'PROMETHEUS_RESPONSE_REQUIRED')
    rows=value['data']['result'];return {'status':'OBSERVED' if rows else 'NO_DATA','result_type':value['data'].get('resultType'),'rows':rows,'missing_is_not_zero':True}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--execute',action='store_true');p.add_argument('--seconds',type=int,default=2400);a=p.parse_args();os.umask(0o077)
    try:
        c=read(a.config);q=c['prometheus'];need(q['labels_reference'] and q['queries'],'ACTUAL_METRIC_LABEL_REFERENCE_REQUIRED')
        u=urllib.parse.urlsplit(q['origin']);suffix=urllib.parse.urlsplit(c['origin']).hostname.removeprefix('seokpan.')
        need(u.scheme=='https' and not u.username and not u.password and u.port is None and u.path in ('','/')
             and not u.query and not u.fragment and u.hostname.endswith('.'+suffix),'MATCHED_NEW_METRICS_ROUTE_REQUIRED')
        need(all(isinstance(x,str) and 0<len(x)<4096 for x in q['queries'].values()),'BOUNDED_QUERIES_REQUIRED')
        if not a.execute:print(json.dumps({'status':'LOCAL_METRIC_QUERY_INPUTS_ONLY','requests':0}));return 0
        need(not a.out.exists(),'NEW_METRIC_OUTPUT_REQUIRED');integer(a.seconds,1,2700,'BOUNDED_METRIC_DURATION_REQUIRED')
        Client(c).identity();tls=ssl.create_default_context(cafile=q.get('ca_file') or None);opener=urllib.request.build_opener(NoRedirect(),urllib.request.HTTPSHandler(context=tls))
        end=min(time.monotonic()+a.seconds,time.monotonic()+stamp(c['window']['not_after'])-time.time())
        while time.monotonic()<end:
            # Refresh the existing personal credential only; never print or persist it.
            token=Client(c).call('whoami','-t').decode().strip();need(token and not any(x.isspace() for x in token),'PERSONAL_TOKEN_REQUIRED')
            row={'utc':utc(),'binding':c['binding'],'config_sha256':digest(c),'queries':{}}
            for name,query in q['queries'].items():
                try:
                    req=urllib.request.Request(q['origin'].rstrip('/')+'/api/v1/query?'+urllib.parse.urlencode({'query':query}),headers={'Authorization':'Bearer '+token})
                    with opener.open(req,timeout=10) as r:data=r.read(1048577)
                    need(len(data)<=1048576,'METRIC_RESPONSE_TOO_LARGE');row['queries'][name]=result(json.loads(data))
                except Exception:row['queries'][name]={'status':'READ_FAILED_NO_RAW_ERROR'}
            token=None;fd=os.open(a.out,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            with os.fdopen(fd,'ab') as f:f.write(canon(row)+b'\n')
            time.sleep(max(0,min(30,end-time.monotonic())))
        print(json.dumps({'status':'COLLECTED_METRIC_RESPONSES_NOT_ZERO_FOR_MISSING_DATA','file':str(a.out)}));return 0
    except Exception as e:print(json.dumps({'status':'STOP_METRIC_COLLECTION','reason':str(e) if type(e) is RuntimeError else type(e).__name__,'raw_token_or_error_printed':False}));return 2

if __name__=='__main__':raise SystemExit(main())
