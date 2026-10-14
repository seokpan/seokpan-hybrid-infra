#!/usr/bin/env python3
"""Human-run ROSA management bootstrap and read-only Cloud binding inventory.
Creates the initial cluster-admin only if absent; never deploys workloads or
changes Terraform/SG rules. Credentials and kubeconfig stay in private files.
"""
import argparse
import datetime
import getpass
import hashlib
import io
import json
import os
import platform
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

class Stop(Exception):
    pass

def require(ok, reason):
    if not ok:
        raise Stop(reason)

def read_json(path):
    require(path.is_file() and not path.is_symlink(), 'PROTECTED_JSON_MISSING_OR_SYMLINK')
    return json.loads(path.read_text())

def write_json(path, value):
    require(not path.is_symlink(), 'OUTPUT_SYMLINK')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(value, f, sort_keys=True, indent=2)
        f.write('\n')
    path.chmod(0o600)

def exclusive_marker(path, value):
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise Stop('PREVIOUS_ADMIN_CREATION_ATTEMPT_REVIEW_REQUIRED')
    with os.fdopen(fd, 'w') as f:
        json.dump(value, f)

def emit(status, **fields):
    print(json.dumps({'status':status, **fields}, sort_keys=True), flush=True)

def validate_admin(value, api):
    require(value.get('api_url','').rstrip('/') == api.rstrip('/') and
            value.get('username') == 'cluster-admin' and
            isinstance(value.get('password'), str) and len(value['password']) >= 12,
            'ADMIN_CREDENTIAL_BINDING_OR_FORMAT')

def validate_nodes(value):
    nodes = value.get('items')
    require(isinstance(nodes, list) and len(nodes) == 9, 'EXPECTED_NINE_NODES_NOT_OBSERVED')
    counts = {'controlplane':0, 'infra':0, 'compute':0, 'ready':0}
    workers = []
    for n in nodes:
        labels = n['metadata'].get('labels',{})
        role = 'controlplane' if any(k in labels for k in ('node-role.kubernetes.io/master','node-role.kubernetes.io/control-plane')) else 'infra' if 'node-role.kubernetes.io/infra' in labels else 'compute' if 'node-role.kubernetes.io/worker' in labels else None
        require(role is not None, 'UNCLASSIFIED_NODE')
        counts[role] += 1
        ready = [c for c in n.get('status',{}).get('conditions',[]) if c.get('type') == 'Ready']
        counts['ready'] += int(len(ready)==1 and ready[0].get('status')=='True')
        if role == 'compute':
            provider = n.get('spec',{}).get('providerID','')
            require(re.fullmatch(r'aws:///ap-northeast-2[a-z]/i-[0-9a-f]{8,17}', provider) is not None, 'WORKER_PROVIDER_ID_NOT_SEOUL_AWS')
            workers.append(provider.rsplit('/',1)[1])
    require(counts == {'controlplane':3,'infra':3,'compute':3,'ready':9}, 'NODE_ROLE_COUNTS_OR_READY_NOT_ACCEPTED')
    require(len(set(workers)) == 3, 'DUPLICATE_WORKER_INSTANCE_ID')
    return counts, workers

def operator_summary(value):
    items=value.get('items',[])
    require(bool(items),'EMPTY_CLUSTER_OPERATOR_LIST')
    pending=[]
    for item in items:
        conditions={c['type']:c['status'] for c in item.get('status',{}).get('conditions',[])}
        if conditions.get('Available')!='True' or conditions.get('Degraded')!='False' or conditions.get('Progressing')!='False':
            pending.append(item['metadata']['name'])
    return {'total':len(items),'not_settled':pending}

def select_worker_group(instances, groups, ids, vpc, account, infra):
    flat=[i for r in instances.get('Reservations',[]) for i in r.get('Instances',[])]
    require(len(flat)==3 and {i['InstanceId'] for i in flat}==set(ids),'WORKER_INSTANCE_SET_CHANGED')
    tag='kubernetes.io/cluster/'+infra
    for i in flat:
        require(i.get('VpcId')==vpc and i.get('State',{}).get('Name')=='running','WORKER_VPC_OR_RUNNING_STATE')
        require({t['Key']:t['Value'] for t in i.get('Tags',[])}.get(tag) in ('owned','shared'),'WORKER_CLUSTER_TAG_MISMATCH')
    candidates=[]
    for g in groups.get('SecurityGroups',[]):
        if g.get('GroupName') != infra+'-worker-sg':
            continue
        tags={t['Key']:t['Value'] for t in g.get('Tags',[])}
        if g.get('VpcId')==vpc and g.get('OwnerId')==account and tags.get(tag) in ('owned','shared') and all(any(s['GroupId']==g['GroupId'] for s in i.get('SecurityGroups',[])) for i in flat):
            candidates.append(g['GroupId'])
    require(len(candidates)==1,'WORKER_SG_NOT_UNIQUELY_PROVEN')
    return candidates[0]

def extract_oc(archive, checksums, destination):
    rows=[]
    for line in checksums.splitlines():
        parts=line.split()
        if len(parts)==2 and parts[1].lstrip('*')=='openshift-client-linux.tar.gz':
            rows.append(parts[0])
    require(len(rows)==1 and re.fullmatch(r'[0-9a-f]{64}',rows[0]) is not None,'OFFICIAL_CLIENT_CHECKSUM_FORMAT')
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    require(digest==rows[0],'OFFICIAL_CLIENT_ARCHIVE_HASH_MISMATCH')
    with tarfile.open(archive,'r:gz') as tf:
        matches=[m for m in tf.getmembers() if m.name in ('oc','./oc') and m.isfile()]
        require(len(matches)==1 and 0<matches[0].size<512*1024*1024,'OC_ARCHIVE_MEMBER_INVALID')
        with tf.extractfile(matches[0]) as source, destination.open('xb') as target:
            shutil.copyfileobj(source,target)
    destination.chmod(0o700)
    return {'archive_sha256':digest,'oc_sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}

class Runner:
    def __init__(self,folder):
        self.folder=folder
        self.env=os.environ.copy()
        for name in list(self.env):
            if name.startswith(('TF_LOG','RHCS_')):
                self.env.pop(name,None)
        self.env.update(AWS_PAGER='',AWS_MAX_ATTEMPTS='1')
        self.phase='LOCAL_CONTEXT'
    def command(self,args,label,timeout=120,input=None,allow_failure=False):
        try:
            r=subprocess.run([str(a) for a in args],input=input,capture_output=True,env=self.env,timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Stop(label+'_TIMEOUT_OUTCOME_REVIEW_REQUIRED')
        (self.folder/(label+'.stderr.log')).write_bytes(r.stderr)
        if r.returncode and not allow_failure:
            (self.folder/(label+'.stdout.log')).write_bytes(r.stdout)
            raise Stop(label+'_FAILED_NO_RAW_OUTPUT')
        return r
    def json_command(self,args,label):
        r=self.command(args,label)
        value=json.loads(r.stdout)
        write_json(self.folder/(label+'.json'),value)
        return value

    def login(self,oc,api,admin,kubeconfig):
        # A pseudo-terminal lets oc read its normal hidden password prompt.
        # The password is never passed as a command argument or printed.
        import pty,select,termios
        master,slave=pty.openpty()
        attrs=termios.tcgetattr(slave)
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(slave,termios.TCSANOW,attrs)
        proc=subprocess.Popen([str(oc),'login',api,'--username','cluster-admin','--kubeconfig',str(kubeconfig),'--request-timeout=20s'],stdin=slave,stdout=slave,stderr=slave,env=self.env,close_fds=True)
        os.close(slave)
        transcript=bytearray();sent=False;deadline=time.monotonic()+75
        try:
            while time.monotonic()<deadline:
                ready,_,_=select.select([master],[],[],0.25)
                if ready:
                    try:chunk=os.read(master,65536)
                    except OSError:chunk=b''
                    if not chunk:break
                    transcript.extend(chunk)
                    if not sent and b'password:' in bytes(transcript).lower():
                        os.write(master,(admin['password']+'\n').encode())
                        sent=True
                if proc.poll() is not None and not ready:break
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
            rc=proc.wait()
        finally:
            os.close(master)
        safe=bytes(transcript).replace(admin['password'].encode(),b'<PASSWORD_REDACTED>')
        (self.folder/'management-login.log').write_bytes(safe)
        if rc:
            if any(x in safe.lower() for x in (b'x509:',b'certificate signed by unknown',b'certificate verify',b'insecure')):
                raise Stop('MANAGEMENT_TLS_ERROR_NO_VERIFICATION_BYPASS')
            require(sent,'OC_PASSWORD_PROMPT_NOT_OBSERVED')
            return False
        return True

def ensure_admin(r, rosa_cli, cluster, api):
    folder = r.folder
    credentials=folder/'initial-admin.json'
    if credentials.exists():
        admin=read_json(credentials);validate_admin(admin,api)
    else:
        described=r.command([rosa_cli,'describe','admin','--cluster',cluster],'admin-description')
        text=(described.stdout+described.stderr).decode(errors='replace')
        require("There is no 'cluster-admin' user" in text,'EXISTING_ADMIN_NEEDS_KNOWN_CREDENTIALS_DO_NOT_RESET')
        exclusive_marker(folder/'admin-create-attempt.json',{'cluster_id':cluster,'action':'CREATE_INITIAL_CLUSTER_ADMIN_ONCE'})
        created=r.command([rosa_cli,'create','admin','--cluster',cluster,'--output','json'],'admin-create',timeout=180)
        admin=json.loads(created.stdout)
        write_json(credentials,admin)
        validate_admin(admin,api)
    return admin

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('input_root',type=Path)
    parser.add_argument('prior_result',type=Path)
    parser.add_argument('rosa_cli',type=Path)
    parser.add_argument('--input-sha256',required=True)
    a=parser.parse_args()
    os.umask(0o077)
    require(os.name=='posix' and os.geteuid()!=0,'USE_PERSONAL_CONTROLLER_LINUX_ACCOUNT')
    require(platform.machine()=='x86_64','LINUX_AMD64_CONTROLLER_REQUIRED')
    require(hashlib.sha256((a.input_root/'protected/inputs.tfvars.json').read_bytes()).hexdigest()==a.input_sha256,'APPROVED_INPUT_FILE_CHANGED')
    marker=read_json(a.prior_result/'cluster-token-rejection-recovery-apply-attempt.json')
    applied=Path(marker['result_directory'])
    require(read_json(applied/'receipt.json').get('status')=='TOKEN_RECOVERY_CLUSTER_APPLY_EXIT_0','SUCCESSFUL_CLUSTER_APPLY_RECEIPT_REQUIRED')
    handoff=read_json(applied/'platform-handoff.json')
    inputs=read_json(a.input_root/'protected/inputs.tfvars.json')
    cluster=handoff['cluster_id'];api=handoff['api_url']
    require(re.fullmatch(r'[a-z0-9]{10,64}',cluster) is not None,'CLUSTER_ID_FORMAT')
    require(isinstance(api,str) and api.startswith('https://api.') and api.endswith('.openshiftapps.com:6443'),'ROSA_API_URL_FORMAT')
    folder=a.input_root/'management'/cluster
    require(not folder.is_symlink(),'MANAGEMENT_FOLDER_SYMLINK')
    folder.mkdir(mode=0o700,parents=True,exist_ok=True);folder.chmod(0o700)
    r=Runner(folder)
    try:
        r.phase='CLI_INSTALL'
        tools=Path.home()/'.local/share/seokpan/oc-cli-4.20'
        tools.mkdir(mode=0o700,parents=True,exist_ok=True)
        oc=tools/'oc';tool_receipt=tools/'receipt.json'
        if oc.exists():
            require(not oc.is_symlink() and read_json(tool_receipt)['oc_sha256']==hashlib.sha256(oc.read_bytes()).hexdigest(),'CACHED_OC_CHANGED_OR_UNTRACKED')
        else:
            emit('INSTALLING_OFFICIAL_OC_4_20')
            base='https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable-4.20/'
            for name in ('sha256sum.txt','openshift-client-linux.tar.gz'):
                with urllib.request.urlopen(base+name,timeout=60) as response,(tools/name).open('wb') as f:
                    shutil.copyfileobj(response,f)
            receipt=extract_oc(tools/'openshift-client-linux.tar.gz',(tools/'sha256sum.txt').read_text(),oc)
            receipt['official_release_channel']='stable-4.20'
            write_json(tool_receipt,receipt)
        emit('PASS_OC_INSTALLED_CHECKSUM_VERIFIED')
        r.phase='TARGET_IDENTITY'
        observed=r.json_command([a.rosa_cli,'describe','cluster','--cluster',cluster,'--output','json'],'rosa-cluster')
        require(observed.get('id')==cluster and observed.get('state')=='ready' and observed.get('api',{}).get('url','').rstrip('/')==api.rstrip('/'),'READY_CLUSTER_OR_API_BINDING_CHANGED')
        require(observed.get('aws',{}).get('account_id')==inputs['foundation']['account_id'],'CLUSTER_AWS_ACCOUNT_CHANGED')
        r.phase='INITIAL_ADMIN'
        admin=ensure_admin(r,a.rosa_cli,cluster,api)
        emit('PASS_INITIAL_ADMIN_CREDENTIALS_PROTECTED',password_printed=False)
        kubeconfig=folder/'kubeconfig'
        require(not kubeconfig.is_symlink(),'KUBECONFIG_SYMLINK')
        r.env['KUBECONFIG']=str(kubeconfig)
        r.phase='MANAGEMENT_LOGIN'
        logged_in=False
        login_deadline=time.monotonic()+600
        for attempt in range(31):
            if time.monotonic()>login_deadline:break
            if r.login(oc,api,admin,kubeconfig):
                logged_in=True;break
            if attempt==30:break
            if attempt%3==0:emit('WAITING_INITIAL_ADMIN_ACTIVATION',elapsed_attempts=attempt+1)
            time.sleep(20)
        require(logged_in,'INITIAL_ADMIN_NOT_ACTIVE_OR_LOGIN_FAILED')
        kubeconfig.chmod(0o600)
        server=r.command([oc,'whoami','--show-server'],'management-server').stdout.decode().strip()
        user=r.command([oc,'whoami'],'management-user').stdout.decode().strip()
        require(server.rstrip('/')==api.rstrip('/') and user=='cluster-admin','MANAGEMENT_TARGET_OR_USER_MISMATCH')
        emit('PASS_ROSA_MANAGEMENT_LOGIN',tls_verification=True)
        activate=folder/'activate.sh'
        activate.write_text('export PATH='+shlex.quote(str(tools))+':"$PATH"\nexport KUBECONFIG='+shlex.quote(str(kubeconfig))+'\n')
        activate.chmod(0o600)
        r.phase='NODE_OPERATOR_READS'
        nodes=r.json_command([oc,'get','nodes','-o','json','--request-timeout=30s'],'nodes')
        counts,worker_ids=validate_nodes(nodes)
        operators=r.json_command([oc,'get','clusteroperators','-o','json','--request-timeout=30s'],'operators')
        operators=operator_summary(operators)
        emit('PASS_NINE_NODES_READY',counts=counts,operators=operators)
        require(not operators['not_settled'],'CLUSTER_OPERATORS_NOT_SETTLED')
        r.phase='CLOUD_WORKER_SG_READS'
        helper=a.input_root/'source/scripts/tf-session.sh'
        raw=helper.read_bytes()
        require(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()=='608a765e2ba40b472b2cb431223b48c396c00866','PURPOSE_AUTH_HELPER_CHANGED')
        code=getpass.getpass('AWS MFA for Cloud Worker SG reads (6 digits): ').strip()
        require(re.fullmatch(r'[0-9]{6}',code) is not None,'MFA_FORMAT')
        shell='set +x; source "$1" rosa >"$2" 2>&1 || exit $?; python3 -c '+shlex.quote("import os,json; print(json.dumps({k:os.environ[k] for k in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN')}))")
        session=r.command(['bash','-c',shell,'purpose-auth',helper,folder/'aws-auth.log'],'purpose-session',input=(code+'\n').encode())
        r.env.update(json.loads(session.stdout));code=''
        aws=['aws','--region','ap-northeast-2','--output','json','--cli-connect-timeout','10','--cli-read-timeout','30']
        caller=r.json_command(aws+['sts','get-caller-identity'],'caller')
        account=inputs['foundation']['account_id']
        require(caller['Arn'].startswith('arn:aws:sts::'+account+':assumed-role/seokpan-tf-rosa/'),'WRONG_CLOUD_READ_ROLE')
        instances=r.json_command(aws+['ec2','describe-instances','--instance-ids',*worker_ids],'worker-instances')
        group_ids=sorted({g['GroupId'] for x in instances['Reservations'] for i in x['Instances'] for g in i.get('SecurityGroups',[])})
        require(bool(group_ids),'WORKER_GROUP_IDS_EMPTY')
        groups=r.json_command(aws+['ec2','describe-security-groups','--group-ids',*group_ids],'worker-security-groups')
        group=select_worker_group(instances,groups,worker_ids,inputs['foundation']['vpc_id'],account,handoff['infra_id'])
        binding={'cluster_id':cluster,'worker_security_group_id':group,'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'evidence_reference':str(folder/'worker-security-groups.json')}
        write_json(folder/'worker-binding-input.json',binding)
        result={'status':'PASS_MANAGEMENT_NODES_AND_CLOUD_WORKER_SG','node_counts':counts,'operators':operators,'worker_sg_unique_cluster_vpc_attachment_match':True,'data_ingress_rules_plan_apply':'NOT_RUN','app_deployment_tls_auth':'NOT_RUN','initial_admin':'CREATED_OR_REUSED_PRIVATE_CREDENTIALS','credential_or_endpoint_values_printed':False}
        write_json(folder/'receipt.json',result);emit(**result)
        print('Private result directory: '+str(folder))
        print('Management activation command: source '+shlex.quote(str(activate)))
    except Exception as exc:
        reason=str(exc) if isinstance(exc,Stop) else type(exc).__name__
        result={'status':'STOP_POST_CREATE_ACCESS','phase':r.phase,'reason':reason,'raw_output':'NOT_EMITTED','repeat_admin_creation':'NOT_PERMITTED_IF_ATTEMPT_MARKER_EXISTS'}
        write_json(folder/'receipt.json',result);emit(**result)
        print('Private result directory: '+str(folder))
        raise SystemExit(1)
    finally:
        for k in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN'):
            r.env.pop(k,None)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        emit('STOP_LOCAL_CONTEXT',reason=str(exc) if isinstance(exc,Stop) else type(exc).__name__);raise SystemExit(1)
