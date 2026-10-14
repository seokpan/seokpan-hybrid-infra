#!/usr/bin/env python3
"""Human-run ROSA management bootstrap and reviewed Cloud Data binding.
Creates the initial cluster-admin only if absent. With --connect-data, applies
only the reviewed two Data ingress rules; never deploys workloads.
Credentials and kubeconfig stay in private files.
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

def validate_cluster_identity(observed, handoff, inputs):
    require(observed.get('id')==handoff['cluster_id'] and observed.get('state')=='ready' and observed.get('api',{}).get('url','').rstrip('/')==handoff['api_url'].rstrip('/'),'READY_CLUSTER_OR_API_BINDING_CHANGED')
    account=inputs['foundation']['account_id']
    require(isinstance(account,str) and re.fullmatch(r'[0-9]{12}',account) is not None,'FOUNDATION_ACCOUNT_FORMAT')
    # ROSA CLI's AWS Account display parses properties.rosa_creator_arn.
    # aws.account_id is optional for STS clusters and must not be required.
    creator=observed.get('properties',{}).get('rosa_creator_arn','')
    match=re.fullmatch(r'arn:aws:sts::([0-9]{12}):assumed-role/seokpan-tf-rosa/[^/]+',creator)
    require(match is not None and match.group(1)==account,'CLUSTER_CREATOR_ACCOUNT_OR_PURPOSE_ROLE_MISMATCH')
    optional=observed.get('aws',{}).get('account_id')
    if optional not in (None,''):
        require(optional==account,'EXPLICIT_CLUSTER_AWS_ACCOUNT_MISMATCH')
    require(observed.get('region',{}).get('id')=='ap-northeast-2','CLUSTER_REGION_CHANGED')
    return {'account_source':'properties.rosa_creator_arn','purpose_creator_account_match':True,'optional_account_field_present':optional not in (None,'')}

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

def diagnose_worker_error(path):
    if not path.is_file() or path.is_symlink():
        return {'classes':['NO_PREVIOUS_LOG'],'raw_output':'NOT_EMITTED'}
    text=path.read_text(errors='replace')
    rules={'ACCESS_DENIED':r'UnauthorizedOperation|AccessDenied|not authorized',
           'EXPIRED_CREDENTIALS':r'ExpiredToken|InvalidClientTokenId|SignatureDoesNotMatch',
           'NETWORK_TLS':r'timed out|Could not connect|SSL validation|certificate verify|NameResolutionError',
           'CLI_ARGUMENTS':r'Unknown options|InvalidParameter|InvalidInstanceID|usage:'}
    classes=[k for k,v in rules.items() if re.search(v,text,re.I)]
    return {'classes':classes or ['UNCLASSIFIED'],'failed_action':'ec2:DescribeInstances','raw_output':'NOT_EMITTED'}

def reference_matches_group(ref, group, account):
    variants=[k for k in ('id','arn','filters') if ref.get(k)]
    if len(variants)!=1:return False
    if variants[0]=='id':return ref['id']==group['GroupId']
    if variants[0]=='arn':return ref['arn']=='arn:aws:ec2:ap-northeast-2:'+account+':security-group/'+group['GroupId']
    tags={t['Key']:t['Value'] for t in group.get('Tags',[])}
    for f in ref['filters']:
        key=f.get('name','');values=f.get('values',[])
        if not isinstance(values,list) or not values or not all(isinstance(v,str) for v in values):return False
        actual=tags.get(key[4:]) if key.startswith('tag:') else {'group-name':group.get('GroupName'),'group-id':group.get('GroupId'),'vpc-id':group.get('VpcId')}.get(key)
        if actual not in values:return False
    return True

def select_machine_worker_group(nodes, machines, groups, vpc, account, infra, diagnostic=None):
    _,ids=validate_nodes(nodes)
    compute={n['metadata']['name']:n for n in nodes['items'] if 'node-role.kubernetes.io/worker' in n['metadata'].get('labels',{}) and not any(k in n['metadata'].get('labels',{}) for k in ('node-role.kubernetes.io/master','node-role.kubernetes.io/control-plane','node-role.kubernetes.io/infra'))}
    linked={}
    for machine in machines.get('items',[]):
        node=machine.get('status',{}).get('nodeRef',{}).get('name')
        if node not in compute:continue
        require(node not in linked,'MULTIPLE_MACHINES_FOR_ONE_COMPUTE_NODE')
        state=machine.get('status',{}).get('providerStatus') or {}
        require(isinstance(state,dict),'MACHINE_PROVIDER_STATUS_FORMAT')
        actual_id=compute[node]['spec']['providerID'].rsplit('/',1)[1]
        require(state.get('instanceId')==actual_id and state.get('instanceState')=='running','MACHINE_NODE_INSTANCE_ID_OR_STATE_MISMATCH')
        value=machine.get('spec',{}).get('providerSpec',{}).get('value',{})
        require(value.get('kind')=='AWSMachineProviderConfig','AWS_MACHINE_PROVIDER_CONFIG_REQUIRED')
        refs=value.get('securityGroups',[])
        require(isinstance(refs,list) and bool(refs),'MACHINE_SECURITY_GROUP_REFERENCES_MISSING')
        linked[node]=refs
    require(set(linked)==set(compute) and len(linked)==3,'THREE_COMPUTE_MACHINES_NOT_LINKED')
    candidates=[];rows=[]
    tag='kubernetes.io/cluster/'+infra
    for number,g in enumerate(groups.get('SecurityGroups',[]),1):
        tags={t['Key']:t['Value'] for t in g.get('Tags',[])}
        row={'group_number':number,'vpc_match':g.get('VpcId')==vpc,
             'account_match':g.get('OwnerId')==account,'cluster_tag_match':tags.get(tag) in ('owned','shared'),
             'legacy_name_assumption_match':infra+'-worker-sg' in (g.get('GroupName'),tags.get('Name')),
             'matching_compute_machine_count':sum(any(reference_matches_group(ref,g,account) for ref in refs) for refs in linked.values())}
        rows.append(row)
        # Machine references, not a guessed installer naming convention, identify
        # the intended group. Keep account/VPC/cluster ownership and uniqueness.
        if all(row[k] for k in ('vpc_match','account_match','cluster_tag_match')) and row['matching_compute_machine_count']==3:
            candidates.append(g['GroupId'])
    if diagnostic is not None:
        diagnostic.update({'linked_compute_machines':len(linked),'queried_group_count':len(rows),
                           'candidate_count':len(candidates),'groups':rows,'values':'NOT_EMITTED',
                           'machine_reference_shapes':[[{'variants':[k for k in ('id','arn','filters') if ref.get(k)],
                               'filter_kinds':[f.get('name') if f.get('name') in ('tag:Name','group-name','group-id','vpc-id') else ('tag:OTHER' if str(f.get('name','')).startswith('tag:') else 'UNSUPPORTED') for f in ref.get('filters',[])]}
                               for ref in refs] for refs in linked.values()]})
    require(len(candidates)==1,'WORKER_SG_NOT_UNIQUELY_PROVEN_FROM_MACHINE_REFERENCES')
    return candidates[0]

def prove_worker_group(nodes, machines, groups, vpc, account, infra, folder, cached=False):
    diagnostic={'stage':'CACHED_LOCAL_PREFLIGHT' if cached else 'CURRENT_READ_ACCEPTANCE'}
    try:
        group=select_machine_worker_group(nodes,machines,groups,vpc,account,infra,diagnostic)
    except Stop as exc:
        diagnostic['reason']=str(exc)
        write_json(folder/'worker-sg-match-diagnostic.json',diagnostic)
        emit('WORKER_SG_MATCH_DIAGNOSTIC',**diagnostic)
        raise
    write_json(folder/'worker-sg-match-diagnostic.json',diagnostic)
    emit('PASS_WORKER_SG_REFERENCE_MATCH',**diagnostic)
    return group

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

def validate_data_plan(plan, original, inputs, binding):
    require(plan.get('complete') is True and plan.get('errored') is not True,'INCOMPLETE_DATA_PLAN')
    require(plan.get('configuration')==original.get('configuration'),'DATA_PLAN_SOURCE_CONFIGURATION_CHANGED')
    oldvars=original.get('variables',{})
    newvars=plan.get('variables',{})
    require(set(oldvars)==set(newvars),'DATA_PLAN_VARIABLE_SET_CHANGED')
    for key in oldvars:
        expected={'value':binding} if key=='worker_sg_binding' else oldvars[key]
        require(newvars[key]==expected,'UNRELATED_INPUT_CHANGED_'+key)
    baseline={x['address'] for x in original['resource_changes'] if x['mode']=='managed'}
    require(len(baseline)==16,'ORIGINAL_RESOURCE_SCOPE_NOT_SIXTEEN')
    prefix='aws_vpc_security_group_ingress_rule.worker_to_data'
    ports={'mariadb':3306,'redis':6379}
    wanted={prefix+'["'+k+'"]':k for k in ports}
    rows=[x for x in plan['resource_changes'] if x['mode']=='managed']
    resources={x['address']:x for x in rows}
    require(len(rows)==len(resources) and set(resources)==baseline|set(wanted),'DATA_PLAN_MANAGED_RESOURCE_SET_CHANGED')
    action_sets=[]
    for address,r in resources.items():
        c=r['change'];a=c.get('after') or {}
        if address in baseline:
            require(c['actions']==['no-op'] and c.get('before')==a,'EXISTING_RESOURCE_MUTATION_IN_DATA_PLAN')
            continue
        key=wanted[address]
        require(c['actions'] in (['create'],['no-op']),'DATA_RULE_UNEXPECTED_ACTION')
        action_sets.append(c['actions'])
        require(a.get('security_group_id')==inputs['foundation']['data_security_groups'][key] and a.get('referenced_security_group_id')==binding['worker_security_group_id'],'DATA_RULE_TARGET_OR_SOURCE_CHANGED')
        require(a.get('ip_protocol')=='tcp' and a.get('from_port')==ports[key] and a.get('to_port')==ports[key],'DATA_RULE_PORT_OR_PROTOCOL_CHANGED')
        require(all(a.get(k) is None for k in ('cidr_ipv4','cidr_ipv6','prefix_list_id')),'DATA_RULE_CIDR_OR_PREFIX_NOT_ALLOWED')
        require(a.get('tags_all')=={'Project':'seokpan','Phase':'2','ManagedBy':'terraform','Component':'rosa'},'DATA_RULE_OWNERSHIP_TAGS_CHANGED')
    require(all(x==['create'] for x in action_sets) or all(x==['no-op'] for x in action_sets),'PARTIAL_PREEXISTING_DATA_BINDING_REQUIRES_REVIEW')
    for r in plan.get('resource_changes',[]):
        if r['mode']=='data':
            require(r['address'] in ('data.aws_caller_identity.current','data.aws_security_group.worker[0]','data.aws_security_group.data["mariadb"]','data.aws_security_group.data["redis"]'),'UNREVIEWED_DATA_SOURCE')
    # Refresh metadata is allowed only when it describes the already-reviewed
    # role-specific attachments or the OIDC null/empty tags representation.
    pairs={r['change']['after']['role']:r['change']['after']['policy_arn'] for r in resources.values() if r.get('type')=='aws_iam_role_policy_attachment'}
    for d in plan.get('resource_drift',[]):
        require(d['address'] in baseline,'DATA_PLAN_UNREVIEWED_DRIFT_RESOURCE')
        b,a=d['change'].get('before'),d['change'].get('after')
        require(isinstance(b,dict) and isinstance(a,dict) and a==resources[d['address']]['change']['after'],'DATA_PLAN_DRIFT_NOT_REFRESHED_NOOP')
        changed=set(b)^set(a)|{k for k in set(b)&set(a) if b[k]!=a[k]}
        allowed=d.get('type')=='aws_iam_openid_connect_provider' and changed=={'tags'} and b.get('tags') in (None,{}) and a.get('tags') in (None,{})
        if d.get('type')=='aws_iam_role' and changed=={'managed_policy_arns'}:
            prior=b.get('managed_policy_arns') or []
            expected=pairs.get(a.get('name'))
            allowed=isinstance(prior,list) and expected is not None and set(prior)<=set([expected]) and a.get('managed_policy_arns')==[expected]
        require(allowed,'DATA_PLAN_UNREVIEWED_PERMISSION_OR_STATE_DRIFT')
    checks=plan.get('checks',[])
    require(bool(checks) and all(x.get('status')=='pass' for x in checks),'DATA_PLAN_CHECK_NOT_PASS')
    outputs=plan.get('output_changes',{})
    require(set(outputs)=={'platform_handoff'},'DATA_PLAN_OUTPUT_SCOPE_CHANGED')
    output=outputs['platform_handoff'];before=output.get('before');after=output.get('after')
    require(isinstance(before,dict) and isinstance(after,dict),'DATA_HANDOFF_OUTPUT_SHAPE')
    expected=dict(before,binding_enabled=True)
    require(after==expected and after.get('cluster_id')==binding['cluster_id'] and after.get('state')=='ready','DATA_HANDOFF_UNRELATED_OUTPUT_CHANGE')
    return {'create':sum(x==['create'] for x in action_sets),'no_op_existing':16,'update':0,'delete':0,'ports':[3306,6379]}

def connect_data(r, root, prior, inputs, binding):
    folder=r.folder/'data-binding'
    folder.mkdir(mode=0o700,exist_ok=True)
    attempted=folder/'apply-attempt.json'
    require(not attempted.exists(),'DATA_APPLY_ALREADY_ATTEMPTED_USE_EXISTING_RESULT')
    source=root/'source'
    hashes={
        'terraform/rosa/.terraform.lock.hcl':'668098ad0f1e293982e9bcf8e931128346a00049',
        'terraform/rosa/backend.tf':'a77421ca8d10663483aba7bc25a01fb949a34ae9',
        'terraform/rosa/bindings.tf':'b0288ba515207445956e7283afa0a1acf34348e3',
        'terraform/rosa/cluster.tf':'74d13092a6cea05cd963779215a24417be3b03cc',
        'terraform/rosa/inputs.tf':'fb45e85d4343d2e3de2378d4099683c251ef30e8',
        'terraform/rosa/oidc.tf':'4e53bfd0fe9176aab8118a87226c59f7dd661fa2',
        'terraform/rosa/outputs.tf':'3a45ccdfc177aae6df384ff35eaff1920dcfca17',
        'terraform/rosa/providers.tf':'fe107451e734f48b16baed2d57b004cd487c8c8c',
        'terraform/rosa/variables.tf':'27b70f1d16700c8f74ea76442e5efce3aee26f5b',
        'terraform/rosa/versions.tf':'9eebe38d8dcc9243a310c3c6fd0a6b011c726da5'}
    for name,expected in hashes.items():
        path=source/name
        require(path.is_file() and not path.is_symlink(),'DATA_SOURCE_FILE_MISSING_OR_SYMLINK')
        raw=path.read_bytes()
        require(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==expected,'DATA_SOURCE_CHANGED')
    tfroot=source/'terraform/rosa'
    require({p.name for p in tfroot.iterdir() if p.name.endswith(('.tf','.tf.json'))}=={Path(k).name for k in hashes if k.endswith(('.tf','.tf.json'))},'UNREVIEWED_TERRAFORM_SOURCE_FILE')
    require(not any(tfroot.glob('*.auto.tfvars*')) and not any((tfroot/n).exists() for n in ('terraform.tfvars','terraform.tfvars.json')),'UNREVIEWED_AUTOMATIC_VARIABLE_FILE')
    r.phase='DATA_PROVIDER_AUTH'
    token=getpass.getpass('Red Hat offline token for Data connection Plan (hidden): ').strip()
    require(bool(token) and not any(c.isspace() for c in token),'DATA_RHCS_TOKEN_FORMAT')
    # Same documented cloud-services client as RHCS 1.7.7; validate before Plan.
    import urllib.parse,urllib.error
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*a,**kw):raise Stop('TOKEN_REDIRECT_REFUSED')
    body=urllib.parse.urlencode({'grant_type':'refresh_token','client_id':'cloud-services','refresh_token':token}).encode()
    req=urllib.request.Request('https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token',data=body,headers={'Content-Type':'application/x-www-form-urlencoded'})
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=30) as response:
        tokens=json.load(response)
    require(isinstance(tokens.get('access_token'),str) and bool(tokens['access_token']),'DATA_TOKEN_EXCHANGE_FAILED')
    for key in list(r.env):
        if key.startswith(('TF_CLI_ARGS','TF_VAR_','TF_LOG','RHCS_')):r.env.pop(key,None)
    for key in ('AWS_PROFILE','AWS_DEFAULT_PROFILE','AWS_ROLE_ARN','AWS_WEB_IDENTITY_TOKEN_FILE','TF_CLI_CONFIG_FILE'):r.env.pop(key,None)
    r.env.update(RHCS_TOKEN=tokens.get('refresh_token') or token,TF_DATA_DIR=str(prior/'tf-data'),TF_WORKSPACE='default',TF_IN_AUTOMATION='1',CHECKPOINT_DISABLE='1',AWS_REGION='ap-northeast-2',AWS_DEFAULT_REGION='ap-northeast-2')
    token='';tokens={}
    newinputs=dict(inputs,worker_sg_binding=binding)
    write_json(folder/'inputs.tfvars.json',newinputs)
    tf=['terraform','-chdir='+str(tfroot)]
    original=r.json_command(tf+['show','-json',str(prior/'rosa.tfplan')],'data-original-plan')
    r.phase='DATA_CONNECTION_FULL_PLAN'
    planfile=folder/'data-binding.tfplan'
    cmd=tf+['plan','-input=false','-no-color','-lock-timeout=60s','-detailed-exitcode','-var-file='+str(folder/'inputs.tfvars.json'),'-out='+str(planfile)]
    result=r.command(cmd,'data-binding-plan',timeout=600,allow_failure=True)
    (folder/'plan.log').write_bytes(result.stdout+result.stderr)
    require(result.returncode in (0,2),'DATA_CONNECTION_PLAN_FAILED_NO_APPLY')
    plan=r.json_command(tf+['show','-json',str(planfile)],'data-binding-plan-json')
    review=validate_data_plan(plan,original,inputs,binding)
    write_json(folder/'review.json',review)
    emit('PASS_DATA_CONNECTION_PLAN_EXACT_SCOPE',**review)
    if review['create']==2:
        digest=hashlib.sha256(planfile.read_bytes()).hexdigest()
        exclusive_marker(attempted,{'plan_sha256':digest,'action':'APPLY_EXACT_TWO_WORKER_TO_DATA_RULES'})
        r.phase='DATA_CONNECTION_APPLY'
        require(hashlib.sha256(planfile.read_bytes()).hexdigest()==digest,'DATA_PLAN_BINARY_CHANGED')
        applied=r.command(tf+['apply','-input=false','-no-color','-lock-timeout=60s',str(planfile)],'data-binding-apply',timeout=600)
        (folder/'apply.log').write_bytes(applied.stdout+applied.stderr)
    latest=r.json_command(tf+['output','-json','platform_handoff'],'data-platform-handoff')
    require(latest.get('cluster_id')==binding['cluster_id'] and latest.get('binding_enabled') is True,'DATA_BINDING_HANDOFF_NOT_CONFIRMED')
    result={'status':'PASS_DATA_CONNECTION_RULES_APPLIED' if review['create']==2 else 'PASS_DATA_CONNECTION_RULES_ALREADY_PRESENT','create':review['create'],'update':0,'delete':0,'ports':[3306,6379],'app_tls_auth_and_business':'NOT_VERIFIED'}
    write_json(folder/'receipt.json',result);emit(**result)
    return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('input_root',type=Path)
    parser.add_argument('prior_result',type=Path)
    parser.add_argument('rosa_cli',type=Path)
    parser.add_argument('--input-sha256',required=True)
    parser.add_argument('--resume-worker-sg',action='store_true',help='Reuse completed management access and continue only Worker SG/Data work')
    parser.add_argument('--expected-aws-user',required=True,help='The human IAM username whose MFA is used')
    parser.add_argument('--connect-data',action='store_true',help='Apply only an automatically reviewed full Plan adding the two Worker-to-Data rules')
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
        # Diagnose previously returned JSON before another API call or MFA prompt.
        # This never authorizes Data writes: current Nodes/Machines/AWS groups are
        # still read and matched below immediately before creating a binding.
        if a.resume_worker_sg and all((folder/name).is_file() for name in ('nodes.json','worker-machine-definitions.json','worker-security-groups.json')):
            r.phase='CACHED_WORKER_SG_MATCH_PREFLIGHT'
            prove_worker_group(read_json(folder/'nodes.json'),read_json(folder/'worker-machine-definitions.json'),
                               read_json(folder/'worker-security-groups.json'),inputs['foundation']['vpc_id'],
                               inputs['foundation']['account_id'],handoff['infra_id'],folder,cached=True)
        if a.resume_worker_sg:
            r.phase='REUSE_COMPLETED_MANAGEMENT'
            tools=Path.home()/'.local/share/seokpan/oc-cli-4.20'
            oc=tools/'oc'
            require(oc.is_file() and not oc.is_symlink() and read_json(tools/'receipt.json')['oc_sha256']==hashlib.sha256(oc.read_bytes()).hexdigest(),'CACHED_OC_CHANGED_OR_MISSING')
            kubeconfig=folder/'kubeconfig'
            require(kubeconfig.is_file() and not kubeconfig.is_symlink(),'EXISTING_ROSA_KUBECONFIG_REQUIRED')
            r.env['KUBECONFIG']=str(kubeconfig)
            server=r.command([oc,'whoami','--show-server'],'management-server').stdout.decode().strip()
            require(server.rstrip('/')==api.rstrip('/'),'EXISTING_MANAGEMENT_TARGET_CHANGED')
            nodes=r.json_command([oc,'get','nodes','-o','json','--request-timeout=30s'],'nodes')
            counts,worker_ids=validate_nodes(nodes)
            operators=operator_summary(read_json(folder/'operators.json'))
            require(not operators['not_settled'],'PREVIOUS_OPERATOR_PASS_RECEIPT_REQUIRED')
            activate=folder/'activate.sh'
            emit('REUSED_MANAGEMENT_ACCESS_AND_CONFIRMED_CURRENT_NODES',counts=counts,admin_creation_repeated=False,oc_installation_repeated=False)
            diagnostic=diagnose_worker_error(folder/'worker-instances.stderr.log')
            write_json(folder/'previous-worker-error-diagnostic.json',diagnostic)
            emit('PREVIOUS_WORKER_QUERY_DIAGNOSTIC',**diagnostic)
        else:
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
            identity=validate_cluster_identity(observed,handoff,inputs)
            emit('PASS_CLUSTER_IDENTITY',**identity)
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
            for attempt in range(11):
                operators=r.json_command([oc,'get','clusteroperators','-o','json','--request-timeout=30s'],'operators')
                operators=operator_summary(operators)
                if not operators['not_settled']:break
                emit('WAITING_CLUSTER_OPERATORS_AFTER_ADMIN_SETUP',not_settled=operators['not_settled'])
                if attempt<10:time.sleep(30)
            emit('PASS_NINE_NODES_READY',counts=counts,operators=operators)
            require(not operators['not_settled'],'CLUSTER_OPERATORS_NOT_SETTLED')
        r.phase='CLOUD_WORKER_SG_READS'
        helper=a.input_root/'source/scripts/tf-session.sh'
        raw=helper.read_bytes()
        require(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()=='608a765e2ba40b472b2cb431223b48c396c00866','PURPOSE_AUTH_HELPER_CHANGED')
        for key in ('AWS_PROFILE','AWS_DEFAULT_PROFILE'):
            r.env.pop(key,None)
        base_args=['env','-u','AWS_ACCESS_KEY_ID','-u','AWS_SECRET_ACCESS_KEY','-u','AWS_SESSION_TOKEN','aws','--region','ap-northeast-2','sts','get-caller-identity','--output','json']
        base=r.json_command(base_args,'base-human-identity')
        expected_user='arn:aws:iam::'+inputs['foundation']['account_id']+':user/'+a.expected_aws_user
        require(base.get('Arn')==expected_user,'BASE_AWS_USER_NOT_EXPECTED_NO_MFA_REQUESTED')
        emit('MFA_FOR_CONFIRMED_HUMAN_AWS_USER',aws_iam_username=a.expected_aws_user)
        code=getpass.getpass('AWS MFA for Cloud Worker SG reads (6 digits): ').strip()
        require(re.fullmatch(r'[0-9]{6}',code) is not None,'MFA_FORMAT')
        shell='set +x; source "$1" rosa >"$2" 2>&1 || exit $?; python3 -c '+shlex.quote("import os,json; print(json.dumps({k:os.environ[k] for k in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN')}))")
        session=r.command(['bash','-c',shell,'purpose-auth',helper,folder/'aws-auth.log'],'purpose-session',input=(code+'\n').encode())
        r.env.update(json.loads(session.stdout));code=''
        aws=['aws','--region','ap-northeast-2','--output','json','--cli-connect-timeout','10','--cli-read-timeout','30']
        caller=r.json_command(aws+['sts','get-caller-identity'],'caller')
        account=inputs['foundation']['account_id']
        require(caller['Arn'].startswith('arn:aws:sts::'+account+':assumed-role/seokpan-tf-rosa/'),'WRONG_CLOUD_READ_ROLE')
        machines=r.json_command([oc,'get','machines.machine.openshift.io','-n','openshift-machine-api','-o','json','--request-timeout=30s'],'worker-machine-definitions')
        groups=r.json_command(aws+['ec2','describe-security-groups','--filters','Name=vpc-id,Values='+inputs['foundation']['vpc_id'],'Name=tag:kubernetes.io/cluster/'+handoff['infra_id']+',Values=owned,shared'],'worker-security-groups')
        group=prove_worker_group(nodes,machines,groups,inputs['foundation']['vpc_id'],account,handoff['infra_id'],folder)
        observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat()
        evidence={'cluster_id':cluster,'infra_id':handoff['infra_id'],'worker_security_group_id':group,'observed_at':observed_at,'proof':'CURRENT_COMPUTE_NODES_LINKED_MACHINE_INSTANCE_IDS_AND_MACHINE_SG_REFERENCES_MATCH_ACTUAL_AWS_GROUP_VPC_OWNER_CLUSTER_TAG','ec2_instance_attachments':'NOT_QUERIED','files_sha256':{name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in ('nodes.json','worker-machine-definitions.json','worker-security-groups.json')}}
        write_json(folder/'worker-binding-evidence.json',evidence)
        binding={'cluster_id':cluster,'worker_security_group_id':group,'observed_at':observed_at,'evidence_reference':str(folder/'worker-binding-evidence.json')}
        write_json(folder/'worker-binding-input.json',binding)
        result={'status':'PASS_MANAGEMENT_NODES_AND_CLOUD_WORKER_SG','node_counts':counts,'operators':operators,'worker_sg_unique_cluster_vpc_machine_reference_match':True,'ec2_instance_attachment_read':'NOT_QUERIED_USE_MACHINE_SPEC_REFERENCE_EVIDENCE','data_ingress_rules_plan_apply':'NOT_RUN','app_deployment_tls_auth':'NOT_RUN','initial_admin':'CREATED_OR_REUSED_PRIVATE_CREDENTIALS','credential_or_endpoint_values_printed':False}
        write_json(folder/'receipt.json',result);emit(**result)
        if a.connect_data:
            data_result=connect_data(r,a.input_root,a.prior_result,inputs,binding)
            result['data_ingress_rules_plan_apply']=data_result['status']
            result['status']='PASS_MANAGEMENT_NODES_WORKER_SG_AND_DATA_RULES'
            write_json(folder/'receipt.json',result)
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
        r.env.pop('RHCS_TOKEN',None)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        emit('STOP_LOCAL_CONTEXT',reason=str(exc) if isinstance(exc,Stop) else type(exc).__name__);raise SystemExit(1)
