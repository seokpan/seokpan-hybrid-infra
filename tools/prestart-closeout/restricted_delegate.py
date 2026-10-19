"""Reviewed legacy capability boundary, using existing run settings instead of host constants."""
import json
import os
from pathlib import Path
import shlex
import stat
import sys

def select(command,c):
    if command=='b-link-probe-v1':return None
    run=Path(c['run']);python=c['python'];args=shlex.split(command)
    allowed=[
        [python,'-I',str(run/'app-measurement/tools/presentation-measurement/rolling_control.py'),
         'trigger','--config',str(run/'measurement-bindings/rolling-config.json'),
         '--work',str(run/'measurement-bindings/rolling-work'),'--execute'],
        [python,'-I',str(run/'hpa-cutover/tools/hpa-load/control.py'),
         'guard','--config',str(run/'hpa.NOT_ACCEPTED.json'),'--work',str(run/'hpa-work'),'--execute']]
    if args not in allowed:raise ValueError('EXACT_MEASUREMENT_COMMAND_REQUIRED')
    return args

def main():
    try:
        path=Path(__file__).with_name('transport-settings.json');info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise ValueError('OWNED_PRIVATE_SETTINGS_REQUIRED')
        c=json.loads(path.read_text(encoding='utf-8'));args=select(os.environ.get('SSH_ORIGINAL_COMMAND',''),c)
        if args is None:
            print(json.dumps({'status':'PASS_GENERATOR_CONTROLLER_NONINTERACTIVE_PROBE',
                'controller_python':c['python'],'resource_mutations':0,'sql_calls':0,
                'rolling_started':False,'recreation_started':False}));return 0
        for i in (2,5):
            p=Path(args[i]);s=p.lstat()
            if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid():raise ValueError('OWNED_EXISTING_TOOL_AND_CONFIG_REQUIRED')
        os.execv(c['python'],args)
    except (OSError,ValueError,KeyError):
        print(json.dumps({'status':'STOP_RESTRICTED_CONTROLLER_COMMAND','resource_mutations':0}));return 2

if __name__=='__main__':raise SystemExit(main())
