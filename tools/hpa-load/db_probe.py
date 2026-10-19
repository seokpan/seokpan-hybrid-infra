"""One current read-only TLS DB sample on the existing permitted host. No secrets exported."""
import argparse
from pathlib import Path
import re
import stat
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import *

SQL="""SHOW SESSION STATUS LIKE 'Ssl_cipher';
SELECT 'GLOBAL_LIMIT',@@GLOBAL.max_connections,@@GLOBAL.max_user_connections;
SHOW GLOBAL STATUS LIKE 'Threads_connected';
SHOW GRANTS FOR CURRENT_USER;
SELECT 'ACCOUNT',USER,COUNT(*) FROM INFORMATION_SCHEMA.PROCESSLIST GROUP BY USER;
"""

def parse(text,binding):
    rows=[x.split('\t') for x in text.splitlines()]
    tls=any(len(x)==2 and x[0]=='Ssl_cipher' and x[1] for x in rows)
    need(tls and any(re.search(r'\b(ALL PRIVILEGES|PROCESS)\b.*\bON\s+\*\.\*',x[0].upper()) for x in rows if len(x)==1),'TLS_AND_GLOBAL_PROCESS_VISIBILITY_REQUIRED')
    limits=next(x for x in rows if x[0]=='GLOBAL_LIMIT' and len(x)==3);limit=int(limits[1]);global_user=int(limits[2])
    total=next(int(x[1]) for x in rows if x[0]=='Threads_connected' and len(x)==2)
    accounts={x[1]:int(x[2]) for x in rows if x[0]=='ACCOUNT' and len(x)==3}
    return {'status':'PASS_CURRENT_DB_SAMPLE','binding':binding,'utc':utc(),'tls':True,'global_limit':limit,'global_max_user_connections':global_user,'connections_total':total,
            'observer_connection_included':True,'per_account':{n:accounts.get(n,0) for n in ('identity_svc','game_svc')},'counts_not_atomic':True}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    try:
        c=read(a.config);d=c['db_local_paths']
        if not a.execute:print(json.dumps({'status':'LOCAL_DB_PROBE_PATHS_ONLY','sql_calls':0}));return 0
        path=Path(d['credential_file']);need(path.is_file() and not path.is_symlink(),'PRIVATE_CLIENT_FILE_REQUIRED')
        if os.name=='posix':need(path.stat().st_uid==os.getuid() and stat.S_IMODE(path.stat().st_mode)&0o077==0,'PRIVATE_OWNED_CREDENTIAL_REQUIRED')
        need(path.stat().st_size<65536 and '[client]' in path.read_text() and not re.search(r'^\s*!include',path.read_text(),re.M),'EXISTING_SINGLE_MARIADB_CLIENT_FILE_REQUIRED')
        need(Path(d['ca_file']).is_file(),'CA_FILE_REQUIRED')
        r=subprocess.run([d['mariadb'],'--defaults-extra-file='+str(path),'--host='+d['database_host'],'--protocol=TCP','--port=3306',
              '--ssl','--ssl-verify-server-cert','--ssl-ca='+d['ca_file'],'--connect-timeout=5','--batch','--skip-column-names','--execute='+SQL],capture_output=True,text=True,timeout=15)
        need(r.returncode==0,'CURRENT_DB_READ_FAILED_NO_RAW_LOG');print(json.dumps(parse(r.stdout,c['binding'])));return 0
    except Exception as e:print(json.dumps({'status':'STOP_DB_PROBE','reason':str(e) if type(e) is RuntimeError else type(e).__name__}));return 2

if __name__=='__main__':raise SystemExit(main())
