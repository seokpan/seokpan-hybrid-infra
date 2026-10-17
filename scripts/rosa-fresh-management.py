"""Reviewed new-environment management and exact two-rule Worker binding adapters."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

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
