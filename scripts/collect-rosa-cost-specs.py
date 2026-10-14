#!/usr/bin/env python3
"""ROSA 비용 원장용 실제 EC2/EBS/LB 조회. 자원/정책/앱 변경 없음.

기존 관리 접속으로 Node와 EC2를 연결하고, tjung의 기존 IAM 인증에서
서울의 지정 조회만 허용하는 15분 임시 세션을 발급한다. 자격은 저장하지
않으며 실제 ID/ARN/주소를 포함한 원문은 보호 폴더에만 저장한다.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

READ_ACTIONS = ['ec2:DescribeInstances', 'ec2:DescribeVolumes',
                'elasticloadbalancing:DescribeLoadBalancers', 'elasticloadbalancing:DescribeTags']


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def read_json(path):
    require(path.is_file() and not path.is_symlink(), 'REGULAR_INPUT_FILE_REQUIRED')
    return json.loads(path.read_text())


def read_policy():
    return {'Version': '2012-10-17', 'Statement': [
        {'Effect': 'Allow', 'Action': READ_ACTIONS, 'Resource': '*',
         'Condition': {'StringEquals': {'aws:RequestedRegion': 'ap-northeast-2'}}},
        {'Effect': 'Deny', 'NotAction': READ_ACTIONS + ['sts:GetCallerIdentity'], 'Resource': '*'},
        {'Effect': 'Deny', 'Action': READ_ACTIONS, 'Resource': '*',
         'Condition': {'StringNotEquals': {'aws:RequestedRegion': 'ap-northeast-2'}}}]}


def node_roles(nodes):
    roles = {}
    for node in nodes.get('items', []):
        labels = node.get('metadata', {}).get('labels', {})
        role = ('controlplane' if any(k in labels for k in ('node-role.kubernetes.io/master', 'node-role.kubernetes.io/control-plane'))
                else 'infra' if 'node-role.kubernetes.io/infra' in labels
                else 'compute' if 'node-role.kubernetes.io/worker' in labels else None)
        provider = node.get('spec', {}).get('providerID', '')
        require(role and re.fullmatch(r'aws:///ap-northeast-2[a-z]/i-[0-9a-f]{8,17}', provider), 'NODE_COST_IDENTITY_UNPROVEN')
        instance = provider.rsplit('/', 1)[1]
        require(instance not in roles, 'DUPLICATE_NODE_INSTANCE')
        roles[instance] = role
    require(roles, 'NO_NODE_INSTANCE_REFERENCES')
    return roles


def instance_rows(instances, volumes, roles, vpc, infra):
    vm = {v['VolumeId']: v for v in volumes}
    observed = set()
    result = []
    tag = 'kubernetes.io/cluster/' + infra
    for i in instances:
        require(i.get('VpcId') == vpc and {t['Key']: t['Value'] for t in i.get('Tags', [])}.get(tag) in ('owned', 'shared'),
                'INSTANCE_CLUSTER_VPC_MISMATCH')
        identity = i['InstanceId']
        require(identity not in observed, 'DUPLICATE_INSTANCE')
        observed.add(identity)
        disks = []
        for block in i.get('BlockDeviceMappings', []):
            vid = block.get('Ebs', {}).get('VolumeId')
            if not vid:
                continue
            require(vid in vm, 'ATTACHED_DISK_NOT_READ')
            v = vm[vid]
            require(any(a.get('InstanceId') == identity and a.get('Device') == block.get('DeviceName') for a in v.get('Attachments', [])),
                    'DISK_ATTACHMENT_MISMATCH')
            disks.append({'gib': v['Size'], 'type': v['VolumeType'],
                          'root': block.get('DeviceName') == i.get('RootDeviceName'),
                          'iops': v.get('Iops'), 'throughput_mibps': v.get('Throughput')})
        result.append({'role': roles.get(identity, 'unmapped_cluster_instance'), 'type': i['InstanceType'],
                       'state': i['State']['Name'], 'ebs': disks})
    require(set(roles) <= observed, 'NODE_INSTANCE_MISSING_FROM_CLUSTER_QUERY')
    return result


def classify_lbs(candidates, tags, infra, family):
    owned = []
    unmatched = 0
    key = 'LoadBalancerArn' if family == 'v2' else 'LoadBalancerName'
    descriptions = {t[key]: {x['Key']: x['Value'] for x in t.get('Tags', [])} for t in tags}
    for lb in candidates:
        require(lb[key] in descriptions, 'LOAD_BALANCER_TAG_RESULT_MISSING')
        if descriptions[lb[key]].get('kubernetes.io/cluster/' + infra) in ('owned', 'shared'):
            owned.append({'type': lb.get('Type', 'classic'), 'scheme': lb.get('Scheme'),
                          'state': lb.get('State', {}).get('Code', 'NOT_EXPOSED_BY_CLASSIC_API')})
        else:
            unmatched += 1
    return owned, unmatched


class Query:
    def __init__(self, folder):
        self.folder = folder
        self.phase = 'LOCAL_INPUTS'
        self.calls = 0

    def json(self, command, label, env, save=True):
        self.phase = label
        self.calls += 1
        r = subprocess.run(command, env=env, capture_output=True, timeout=55, stdin=subprocess.DEVNULL)
        (self.folder / (label + '.stderr')).write_bytes(r.stderr)
        if r.returncode:
            message = r.stderr.decode(errors='replace')
            kind = 'ACCESS_DENIED' if re.search(r'AccessDenied|UnauthorizedOperation|not authorized', message, re.I) else 'QUERY_FAILED'
            raise ValueError(label.upper() + '_' + kind)
        value = json.loads(r.stdout)
        if save:
            (self.folder / (label + '.json')).write_bytes(r.stdout)
        return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input_root', type=Path)
    p.add_argument('--input-sha256', required=True)
    p.add_argument('--expected-aws-user', default='tjung')
    a = p.parse_args()
    folder = None
    q = None
    env = None
    credentials = None
    try:
        os.umask(0o077)
        require(os.name == 'posix' and __import__('pwd').getpwuid(os.getuid()).pw_name == 'jth', 'USE_JTH_CONTROLLER')
        root = a.input_root.resolve()
        raw = (root / 'protected/inputs.tfvars.json').read_bytes()
        require(hashlib.sha256(raw).hexdigest() == a.input_sha256, 'ACCEPTED_INPUTS_CHANGED')
        inputs = json.loads(raw)
        account, vpc = inputs['foundation']['account_id'], inputs['foundation']['vpc_id']
        require(re.fullmatch(r'\d{12}', account) and re.fullmatch(r'vpc-[0-9a-f]{8,17}', vpc), 'FOUNDATION_IDENTITY_FORMAT')
        handoffs = [path for path in (root / 'management').glob('*/data-platform-handoff.json')
                    if read_json(path).get('binding_enabled') is True]
        require(len(handoffs) == 1, 'UNIQUE_COMPLETED_PLATFORM_REQUIRED')
        handoff = read_json(handoffs[0])
        require(handoff['cluster_id'] == handoffs[0].parent.name, 'PLATFORM_CLUSTER_BINDING')
        infra = handoff['infra_id']
        require(re.fullmatch(r'[a-z0-9-]{3,64}', infra), 'CLUSTER_INFRA_ID_FORMAT')
        folder = Path(tempfile.mkdtemp(prefix='rosa-actual-cost-specs-', dir=root))
        q = Query(folder)
        base_env = dict(os.environ, AWS_PAGER='', AWS_MAX_ATTEMPTS='1')
        for k in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'AWS_SECURITY_TOKEN',
                  'AWS_PROFILE', 'AWS_DEFAULT_PROFILE', 'AWS_ROLE_ARN', 'AWS_WEB_IDENTITY_TOKEN_FILE'):
            base_env.pop(k, None)
        aws = ['aws', '--region', 'ap-northeast-2', '--output', 'json', '--cli-connect-timeout', '10', '--cli-read-timeout', '30']
        caller = q.json(aws + ['sts', 'get-caller-identity'], 'base-caller', base_env)
        require(caller.get('Arn') == 'arn:aws:iam::' + account + ':user/' + a.expected_aws_user,
                'EXPECTED_HUMAN_CALLER_REQUIRED_NO_FALLBACK')
        name = 'seokpan-cost-read-' + hashlib.sha256(folder.name.encode()).hexdigest()[:8]
        policy = json.dumps(read_policy(), separators=(',', ':'))
        require(len(policy) <= 2048, 'READ_SESSION_POLICY_SIZE')
        session = q.json(aws + ['sts', 'get-federation-token', '--name', name, '--duration-seconds', '900', '--policy', policy],
                         'restricted-read-session', base_env, save=False)
        expected = 'arn:aws:sts::' + account + ':federated-user/' + name
        require(session.get('FederatedUser', {}).get('Arn') == expected, 'READ_SESSION_IDENTITY')
        credentials = session.pop('Credentials')
        env = dict(base_env, AWS_ACCESS_KEY_ID=credentials['AccessKeyId'], AWS_SECRET_ACCESS_KEY=credentials['SecretAccessKey'],
                   AWS_SESSION_TOKEN=credentials['SessionToken'])
        require(q.json(aws + ['sts', 'get-caller-identity'], 'read-session-caller', env).get('Arn') == expected, 'READ_SESSION_CALLER')
        oc = Path.home() / '.local/share/seokpan/oc-cli-4.20/oc'
        kube = handoffs[0].parent / 'kubeconfig'
        context = q.json([str(oc), '--kubeconfig', str(kube), 'config', 'view', '--minify', '-o', 'json'], 'context', base_env)
        require(context['clusters'][0]['cluster']['server'].rstrip('/') == handoff['api_url'].rstrip('/'), 'WRONG_MANAGEMENT_CONTEXT')
        nodes = q.json([str(oc), '--kubeconfig', str(kube), 'get', 'nodes', '-o', 'json', '--request-timeout=20s'], 'node-cost-references', base_env)
        roles = node_roles(nodes)
        observed = q.json(aws + ['ec2', 'describe-instances', '--filters', 'Name=vpc-id,Values=' + vpc,
                         'Name=tag:kubernetes.io/cluster/' + infra + ',Values=owned,shared',
                         'Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down'], 'instances', env)
        instances = [i for reservation in observed.get('Reservations', []) for i in reservation.get('Instances', [])]
        require(instances, 'NO_CLUSTER_INSTANCES')
        volumes = q.json(aws + ['ec2', 'describe-volumes', '--filters', 'Name=attachment.instance-id,Values=' +
                               ','.join(i['InstanceId'] for i in instances)], 'volumes', env).get('Volumes', [])
        rows = instance_rows(instances, volumes, roles, vpc, infra)
        print(json.dumps({'status': 'PASS_ACTUAL_EC2_EBS_COST_SPECS', 'instances': rows,
                          'resource_changes': 0}, sort_keys=True), flush=True)
        lbs, unmatched = [], 0
        for family, service, listkey in (('v2', 'elbv2', 'LoadBalancers'), ('classic', 'elb', 'LoadBalancerDescriptions')):
            response = q.json(aws + [service, 'describe-load-balancers'], service + '-list', env)
            candidates = [lb for lb in response.get(listkey, []) if lb.get('VpcId', lb.get('VPCId')) == vpc]
            alltags = []
            key, argument = ('LoadBalancerArn', '--resource-arns') if family == 'v2' else ('LoadBalancerName', '--load-balancer-names')
            for start in range(0, len(candidates), 20):
                batch = candidates[start:start + 20]
                tags = q.json(aws + [service, 'describe-tags', argument, *[lb[key] for lb in batch]], service + '-tags-' + str(start), env)
                if family == 'v2':
                    alltags += [dict(t, LoadBalancerArn=t['ResourceArn']) for t in tags.get('TagDescriptions', [])]
                else:
                    alltags += tags.get('TagDescriptions', [])
            owned, unknown = classify_lbs(candidates, alltags, infra, family)
            lbs += owned
            unmatched += unknown
        summary = {'status': 'PASS_ACTUAL_ROSA_COST_SPECS' if not unmatched and all(r['role'] != 'unmapped_cluster_instance' for r in rows)
                   else 'PARTIAL_ACTUAL_ROSA_COST_SPECS_CLASSIFICATION_REQUIRED',
                   'observed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   'instances': rows, 'load_balancers': lbs, 'unclassified_same_vpc_load_balancers': unmatched,
                   'total_instance_count': len(rows), 'cluster_tagged_lb_count': len(lbs),
                   'api_commands': q.calls, 'iam_policy_changes': 0, 'resource_changes': 0,
                   'temporary_read_session_seconds': 900, 'credentials_saved': False,
                   'scope': 'CURRENT_ATTACHED_EBS_AND_CLUSTER_OWNED_LB_NOT_ENTIRE_FOUNDATION_COST',
                   'prices_or_total_cost': 'NOT_CALCULATED_D_LEDGER_INPUT_ONLY'}
        (folder / 'summary.json').write_text(json.dumps(summary, sort_keys=True))
        print(json.dumps(summary, sort_keys=True))
        print('Private cost evidence directory: ' + str(folder))
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r'[A-Z0-9_-]+', str(exc)) else type(exc).__name__
        print(json.dumps({'status': 'STOP_ACTUAL_COST_SPEC_READ', 'phase': q.phase if q else 'LOCAL_INPUTS',
                          'reason': reason, 'resource_changes': 0, 'iam_policy_changes': 0, 'automatic_retry': False}, sort_keys=True))
        if folder:
            print('Private cost evidence directory: ' + str(folder))
        return 1
    finally:
        if credentials: credentials.clear()
        if env:
            for k in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN'): env.pop(k, None)


if __name__ == '__main__': sys.exit(main())
