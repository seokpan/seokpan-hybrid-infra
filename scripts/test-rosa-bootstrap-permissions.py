#!/usr/bin/env python3
# 작성자: 이유빈
# 작성일: 2026-10-09
# 작성내용: ROSA bootstrap 실행 권한의 격리 모의시험 (#47)
#
# AWS 자원은 모의 객체를 사용합니다.
# IAM 문서만 기존 Provider의 로컬 문서 생성기로 계산합니다.
# 실제 인증정보·Backend·State·AWS API를 사용하지 않습니다.
# 구조 검사는 실제 AWS 실효 권한 검증을 대신하지 않습니다.

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
bootstrap = repo / "terraform/bootstrap"
foundation = repo / "terraform/foundation"


def stop(message):
    raise SystemExit("중단: " + message)


def invoke(arguments, cwd, env, input_text=None):
    return subprocess.run(
        arguments,
        cwd=cwd,
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
    )


env = {
    key: value
    for key, value in os.environ.items()
    if not key.startswith(("AWS_", "TF_"))
}
env.update({
    "CHECKPOINT_DISABLE": "1",
    "TF_IN_AUTOMATION": "1",
    "AWS_EC2_METADATA_DISABLED": "true",
})

result = invoke(["terraform", "version", "-json"], foundation, env)
if result.returncode:
    stop("기존 Terraform 버전을 확인하지 못했습니다.")

version = json.loads(result.stdout)
if version["terraform_version"] != "1.16.4":
    stop("기존 Terraform 1.16.4가 필요합니다.")

platform = version["platform"]
provider_folder = (
    Path(os.environ.get("SEOKPAN_TEST_PROVIDER_ROOT", str(foundation)))
    / ".terraform/providers/registry.terraform.io/hashicorp/aws"
    / "6.67.0"
    / platform
)
binaries = [
    path
    for path in provider_folder.glob("terraform-provider-aws*")
    if path.is_file() and os.access(path, os.X_OK)
]
if len(binaries) != 1:
    stop("기존 AWS Provider 6.67.0 실행 파일을 확인해야 합니다.")

iam_path = bootstrap / "iam.tf"
execution_path = bootstrap / "rosa_iam_execution.tf"
read_path = bootstrap / "rosa_plan_read_permissions.tf"
cluster_path = bootstrap / "rosa_cluster_iam_execution.tf"
data_path = bootstrap / "rosa_data_connection_permissions.tf"
lock = bootstrap / ".terraform.lock.hcl"

for path in (iam_path, execution_path, read_path, cluster_path, data_path, lock):
    if path.is_symlink() or not path.is_file():
        stop("기존 bootstrap 코드 또는 잠금 파일을 확인해야 합니다.")

iam = iam_path.read_text(encoding="utf-8")
execution = execution_path.read_text(encoding="utf-8")
read_source = read_path.read_text(encoding="utf-8")
cluster_source = cluster_path.read_text(encoding="utf-8")
data_source = data_path.read_text(encoding="utf-8")

inline_declarations = set()
for path in bootstrap.glob("*.tf"):
    inline_declarations.update(re.findall(
        r'resource\s+"aws_iam_role_policy"\s+"([^"]+)"',
        path.read_text(encoding="utf-8"),
    ))

if inline_declarations != {
    "tf_bootstrap",
    "tf_backend",
    "tf_foundation_registry_ci",
    "tf_foundation_data",
    "tf_rosa_first_plan_read",
    "tf_rosa_cluster_iam",
    "tf_rosa_data_connection",
}:
    stop("개별 역할 정책 선언이 달라졌습니다. 합계 검사 범위를 보완해야 합니다.")

result = invoke(
    ["git", "merge-base", "HEAD", "origin/main"], repo, env
)
if result.returncode:
    stop("main과의 공통 기준 기록을 확인해야 합니다.")

base_revision = result.stdout.strip()
baseline = invoke(
    ["git", "show", base_revision + ":terraform/bootstrap/iam.tf"],
    repo,
    env,
)
if baseline.returncode:
    stop("기존 IAM 권한의 기준 파일을 확인해야 합니다.")

hook = """  source_policy_documents = (
    var.enable_rosa_account_iam_permissions
    ? [data.aws_iam_policy_document.tf_bootstrap_rosa_iam.json]
    : []
  )
"""
if iam.count(hook) != 1:
    stop("새 권한의 조건부 연결 위치가 예상과 다릅니다.")


def canonical_source(text):
    result = invoke(
        ["terraform", "fmt", "-no-color", "-"], repo, env, text
    )
    if result.returncode:
        stop("IAM 기준 코드의 형식을 확인하지 못했습니다.")
    return "\n".join(
        line.rstrip()
        for line in result.stdout.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


# NAT EIP 속성 조회 한 항목만 기준 비교에서 구분합니다.
attribute_read_line = '        "ec2:DescribeAddressesAttribute",\n'
if iam.count(attribute_read_line) != 1:
    stop("EIP 속성 조회 권한 한 항목이 필요합니다.")
if baseline.stdout.count(attribute_read_line) not in (0, 1):
    stop("기준 코드의 EIP 속성 조회 선언 수가 다릅니다.")
comparison_iam = iam.replace(attribute_read_line, "", 1)
comparison_baseline = baseline.stdout.replace(attribute_read_line, "", 1)
if canonical_source(comparison_iam.replace(hook, "", 1)) != canonical_source(
    comparison_baseline.replace(hook, "", 1)
):
    stop("조건부 연결 외에 기존 IAM 선언의 차이가 있습니다. 별도 검토가 필요합니다.")

print("통과: 조건부 연결 외 기존 IAM 선언 유지", flush=True)
print("비교 기준 저장 기록:", base_revision[:12], flush=True)

# 기대값은 검사 대상 코드의 locals를 재사용하지 않고 별도로 정의합니다.
account = "000000000000"

# 실제 코드의 네트워크 조회 문장에서 새 작업의 지역 범위를 확인합니다.
network_match = re.search(
    r'data "aws_iam_policy_document" "tf_foundation_network"\s*\{'
    r'.*?<<-POLICY\n(.*?)\n\s*POLICY', iam, re.S
)
if not network_match:
    stop("네트워크 조회 정책 문서를 찾지 못했습니다.")
network_document = json.loads(
    network_match.group(1).replace("${local.account_id}", account)
)
attribute_statements = [
    statement for statement in network_document["Statement"]
    if "ec2:DescribeAddressesAttribute" in statement.get("Action", [])
]
if len(attribute_statements) != 1:
    stop("EIP 속성 조회 권한의 선언 범위가 다릅니다.")
attribute_statement = attribute_statements[0]
if (
    attribute_statement.get("Sid") != "ReadFoundationNetwork"
    or attribute_statement.get("Effect") != "Allow"
    or attribute_statement.get("Resource") != ["*"]
    or attribute_statement.get("Condition") != {
        "StringEquals": {"aws:RequestedRegion": "ap-northeast-2"}
    }
):
    stop("EIP 속성 조회의 지역 제한 또는 조건이 다릅니다.")
for region, expected_allowed in [
    ("ap-northeast-2", True),
    ("ap-northeast-1", False),
    ("", False),
]:
    allowed = (
        region == attribute_statement["Condition"]["StringEquals"]["aws:RequestedRegion"]
    )
    if allowed != expected_allowed:
        stop("EIP 속성 조회 허용·차단 사례가 다릅니다.")
print("통과: EIP 속성 조회 한 항목·서울 허용·다른 리전과 지역 누락 차단", flush=True)

role_names = {
    "installer": "seokpan-fnd-rosa-Installer-Role",
    "support": "seokpan-fnd-rosa-Support-Role",
    "controlplane": "seokpan-fnd-rosa-ControlPlane-Role",
    "worker": "seokpan-fnd-rosa-Worker-Role",
}
role_arns = {
    key: f"arn:aws:iam::{account}:role/{name}"
    for key, name in role_names.items()
}
account_policy_arns = {
    key: f"arn:aws:iam::{account}:policy/{name}-Policy"
    for key, name in role_names.items()
}
operator_names = [
    "seokpan-fnd-rosa-openshift-cloud-credential-operator-cloud-crede",
    "seokpan-fnd-rosa-openshift-cloud-network-config-controller-cloud",
    "seokpan-fnd-rosa-openshift-cluster-csi-drivers-ebs-cloud-credent",
    "seokpan-fnd-rosa-openshift-image-registry-installer-cloud-creden",
    "seokpan-fnd-rosa-openshift-ingress-operator-cloud-credentials",
    "seokpan-fnd-rosa-openshift-machine-api-aws-cloud-credentials",
]
policy_arns = list(account_policy_arns.values()) + [
    f"arn:aws:iam::{account}:policy/{name}"
    for name in operator_names
]
management_arn = (
    f"arn:aws:iam::{account}:policy/seokpan-tf-foundation-rosa-iam"
)
foundation_role = (
    f"arn:aws:iam::{account}:role/seokpan-tf-foundation"
)
policy_actions = [
    "iam:CreatePolicy",
    "iam:GetPolicy",
    "iam:GetPolicyVersion",
    "iam:ListPolicyVersions",
    "iam:CreatePolicyVersion",
    "iam:SetDefaultPolicyVersion",
    "iam:DeletePolicyVersion",
    "iam:DeletePolicy",
    "iam:TagPolicy",
    "iam:UntagPolicy",
    "iam:ListPolicyTags",
]


def expected_statement(actions, resources, condition=None):
    condition = condition or {}
    return {
        "Effect": "Allow",
        "Action": sorted(actions),
        "Resource": sorted(resources),
        "Condition": condition,
        "Keys": sorted(
            ["Sid", "Effect", "Action", "Resource"]
            + (["Condition"] if condition else [])
        ),
    }


expected_foundation = {
    "ReadRosaAccountRoles": expected_statement([
        "iam:GetRole",
        "iam:GetRolePolicy",
        "iam:ListRolePolicies",
        "iam:ListAttachedRolePolicies",
        "iam:ListInstanceProfilesForRole",
        "iam:ListRoleTags",
    ], list(role_arns.values())),
    "CreateRosaAccountRoles": expected_statement(
        ["iam:CreateRole"],
        list(role_arns.values()),
        {"Null": {"iam:PermissionsBoundary": "true"}},
    ),
    "ManageRosaAccountRoles": expected_statement([
        "iam:DeleteRole",
        "iam:UpdateRole",
        "iam:UpdateAssumeRolePolicy",
        "iam:TagRole",
        "iam:UntagRole",
    ], list(role_arns.values())),
    "ManageRosaOfficialPolicies": expected_statement(
        policy_actions, policy_arns
    ),
}

for key, name in role_names.items():
    sid = "Attach" + name.replace("-", "") + "Policy"
    expected_foundation[sid] = expected_statement(
        ["iam:AttachRolePolicy", "iam:DetachRolePolicy"],
        [role_arns[key]],
        {"ArnEquals": {"iam:PolicyARN": account_policy_arns[key]}},
    )

expected_bootstrap = {
    "ManageFoundationRosaIamPolicy": expected_statement(
        policy_actions, [management_arn]
    ),
    "AttachFoundationRosaIamPolicy": expected_statement(
        ["iam:AttachRolePolicy", "iam:DetachRolePolicy"],
        [foundation_role],
        {"ArnEquals": {"iam:PolicyARN": management_arn}},
    ),
}


import fnmatch

def check_cluster_scope(document, account, policy_names):
    statements = document.get("Statement", [])
    tails = [
        "openshift-cloud-credential-operator-cloud-crede",
        "openshift-cloud-network-config-controller-cloud",
        "openshift-cluster-csi-drivers-ebs-cloud-credent",
        "openshift-image-registry-installer-cloud-creden",
        "openshift-ingress-operator-cloud-credentials",
        "openshift-machine-api-aws-cloud-credentials",
    ]
    roles = [
        "arn:aws:iam::" + account + ":role/seokpan-cloud-op-" + tail
        for tail in tails
    ]
    policies = [
        "arn:aws:iam::" + account + ":policy/seokpan-fnd-rosa-" + tail
        for tail in tails
    ]
    role_actions = [
        "iam:CreateRole", "iam:GetRole", "iam:TagRole",
        "iam:ListRolePolicies", "iam:GetRolePolicy",
        "iam:ListAttachedRolePolicies",
        "iam:ListInstanceProfilesForRole", "iam:DeleteRole",
    ]
    oidc_actions = [
        "iam:CreateOpenIDConnectProvider",
        "iam:GetOpenIDConnectProvider",
        "iam:TagOpenIDConnectProvider",
        "iam:ListOpenIDConnectProviderTags",
        "iam:DeleteOpenIDConnectProvider",
    ]

    def values(value):
        return value if isinstance(value, list) else [value]

    expected_actions = set(
        role_actions + oidc_actions
        + ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
    )
    actual_actions = {
        action
        for statement in statements
        for action in values(statement.get("Action", []))
    }
    if actual_actions != expected_actions:
        raise ValueError("새 정책의 작업 목록이 요청 범위와 다릅니다.")

    for statement in statements:
        if set(statement) - {
            "Sid", "Effect", "Action", "Resource", "Condition"
        }:
            raise ValueError("검사하지 못하는 정책 문법이 있습니다.")

    def allowed(action, resource, context=None):
        context = {
            key.lower(): value
            for key, value in (context or {}).items()
        }
        effects = []

        for statement in statements:
            if not any(
                fnmatch.fnmatchcase(action.lower(), item.lower())
                for item in values(statement.get("Action", []))
            ):
                continue

            if not any(
                fnmatch.fnmatchcase(resource, item)
                for item in values(statement.get("Resource", []))
            ):
                continue

            matched = True

            for operator, entries in statement.get("Condition", {}).items():
                if operator not in (
                    "StringEquals", "StringEqualsIfExists", "ArnEquals"
                ):
                    raise ValueError("조건 연산자의 검사 보완이 필요합니다.")

                for key, expected in entries.items():
                    present = key.lower() in context

                    if not present:
                        if operator != "StringEqualsIfExists":
                            matched = False
                    elif context[key.lower()] not in values(expected):
                        matched = False

            if matched:
                effects.append(statement.get("Effect"))

        return "Allow" in effects and "Deny" not in effects

    cases = []

    def case(label, expected, action, resource, context=None):
        cases.append(
            (label, expected, allowed(action, resource, context))
        )

    other_account = (
        "111111111111"
        if account != "111111111111"
        else "222222222222"
    )

    for role in roles:
        for action in role_actions:
            case("지정 역할 작업 허용", True, action, role)

    for action in role_actions:
        case(
            "다른 역할 거부", False, action,
            "arn:aws:iam::" + account + ":role/unrelated-role"
        )
        case(
            "다른 IAM 경로 거부", False, action,
            roles[0].replace(":role/", ":role/other/")
        )
        case(
            "다른 계정 역할 거부", False, action,
            roles[0].replace(account, other_account)
        )

    for index, role in enumerate(roles):
        for action in ("iam:AttachRolePolicy", "iam:DetachRolePolicy"):
            for policy_index, policy in enumerate(policies):
                case(
                    "역할별 짝 정책 제한",
                    index == policy_index,
                    action, role,
                    {"iam:PolicyARN": policy}
                )

            case("정책 조건 누락 거부", False, action, role)
            case(
                "다른 정책 거부", False, action, role,
                {
                    "iam:PolicyARN":
                        "arn:aws:iam::" + account + ":policy/unrelated-policy"
                }
            )

    for action in (
        "iam:PassRole", "iam:PutRolePolicy",
        "iam:UpdateAssumeRolePolicy", "iam:CreatePolicy"
    ):
        case("요청하지 않은 작업 거부", False, action, roles[0])

    oidc = (
        "arn:aws:iam::" + account
        + ":oidc-provider/example.invalid/project"
    )
    tags = {
        "Project": "seokpan",
        "Phase": "2",
        "Component": "rosa",
    }
    request_tags = {
        "aws:RequestTag/" + key: value
        for key, value in tags.items()
    }
    resource_tags = {
        "aws:ResourceTag/" + key: value
        for key, value in tags.items()
    }

    case(
        "생성 요청 태그 일치", True,
        "iam:CreateOpenIDConnectProvider", oidc, request_tags
    )

    for key in request_tags:
        missing = dict(request_tags)
        missing.pop(key)
        wrong = dict(request_tags)
        wrong[key] = "other"

        case(
            "생성 요청 태그 누락 거부", False,
            "iam:CreateOpenIDConnectProvider", oidc, missing
        )
        case(
            "생성 요청 태그 불일치 거부", False,
            "iam:CreateOpenIDConnectProvider", oidc, wrong
        )

    for action in (
        "iam:GetOpenIDConnectProvider",
        "iam:ListOpenIDConnectProviderTags",
        "iam:DeleteOpenIDConnectProvider"
    ):
        case("소유 OIDC 관리 허용", True, action, oidc, resource_tags)
        case("태그 없는 기존 OIDC 관리 거부", False, action, oidc)

        for key in resource_tags:
            wrong = dict(resource_tags)
            wrong[key] = "other"
            case("다른 소유 OIDC 관리 거부", False, action, oidc, wrong)

    case(
        "소유 OIDC 태그 변경 허용", True,
        "iam:TagOpenIDConnectProvider", oidc,
        {**resource_tags, **request_tags}
    )
    case(
        "소유 OIDC 일반 태그 추가 허용", True,
        "iam:TagOpenIDConnectProvider", oidc, resource_tags
    )
    case(
        "소유 표시 변경 거부", False,
        "iam:TagOpenIDConnectProvider", oidc,
        {
            **resource_tags,
            **request_tags,
            "aws:RequestTag/Project": "other",
        }
    )
    case(
        "태그 없는 기존 OIDC 표시 추가 거부", False,
        "iam:TagOpenIDConnectProvider", oidc, request_tags
    )
    case(
        "다른 계정 OIDC 생성 거부", False,
        "iam:CreateOpenIDConnectProvider",
        oidc.replace(account, other_account), request_tags
    )

    # 태그 포함 생성 요청에도 자원 태그가 제공된다는 공식 문서를 반영합니다.
    # https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_condition-keys.html
    creation_context = {**resource_tags, **request_tags}
    case('태그 포함 생성 요청 허용', True, 'iam:CreateOpenIDConnectProvider', oidc, creation_context)
    case('태그 포함 생성의 태그 권한 허용', True, 'iam:TagOpenIDConnectProvider', oidc, creation_context)
    for key in list(request_tags) + list(resource_tags):
        missing = dict(creation_context)
        missing.pop(key)
        wrong = dict(creation_context)
        wrong[key] = 'other'
        # 요청 태그 누락은 생성 작업에서 검사합니다.
        # 자원 태그 누락은 태그 작업에서 검사합니다.
        action = 'iam:CreateOpenIDConnectProvider' if key in request_tags else 'iam:TagOpenIDConnectProvider'
        case('생성 맥락의 필수 태그 누락 차단', False, action, oidc, missing)
        case('생성 맥락의 필수 태그 불일치 차단', False, 'iam:TagOpenIDConnectProvider', oidc, wrong)
    failures = [
        label
        for label, expected, actual in cases
        if expected != actual
    ]

    creation_tag_allowed = allowed('iam:TagOpenIDConnectProvider', oidc, creation_context)
    print('태그 없는 기존 자원의 표시 추가 차단:', '통과' if not allowed('iam:TagOpenIDConnectProvider', oidc, request_tags) else '실패')

    print("새 정책의 오프라인 허용·차단 사례:", len(cases), "개")
    print("예상과 일치한 사례:", len(cases) - len(failures), "개")
    print("예상과 다른 사례:", len(failures), "개")
    print(
        "태그 포함 생성 맥락의 태그 부착 허용:",
        "예" if creation_tag_allowed else "아니오"
    )
    print("확인: 태그 포함 생성과 태그 없는 기존 자원을 구분해 검사했습니다.")
    print("확인: 공식 문서에 따른 모의검사이며 실제 생성 성공 확인은 별도입니다.")
    print("확인: 실제 AWS 정책 시뮬레이터나 생성 시험은 아닙니다.")

    if failures:
        raise ValueError("허용·차단 사례가 요청 범위와 다릅니다.")

    return creation_tag_allowed

cluster_test_policies = {
    name: f"arn:aws:iam::{account}:policy/{name}"
    for name in operator_names
}

fixtures = r'''
# 이 Provider는 로컬 IAM 문서 생성에만 사용합니다.
# 자원과 호출자 조회는 모의 Provider가 처리합니다.
provider "aws" {
  alias                       = "offline_documents"
  region                      = "ap-northeast-2"
  access_key                  = "AKIA0000000000000000"
  secret_key                  = "0000000000000000000000000000000000000000"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
  skip_region_validation      = true
  shared_credentials_files    = []
  shared_config_files         = []
  endpoints {
    iam = "http://127.0.0.1:9"
    sts = "http://127.0.0.1:9"
  }
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "tfstate" {
  bucket = "seokpan-test-state"
}

locals {
  test_documents = {
    foundation = jsondecode(data.aws_iam_policy_document.tf_foundation_rosa_iam.json)
    bootstrap  = jsondecode(data.aws_iam_policy_document.tf_bootstrap_rosa_iam.json)
  }

  test_normalized_documents = {
    for name, document in local.test_documents : name => {
      for statement in document.Statement : statement.Sid => {
        Effect    = statement.Effect
        Action    = sort(try(tolist(statement.Action), [tostring(statement.Action)]))
        Resource  = sort(try(tolist(statement.Resource), [tostring(statement.Resource)]))
        Condition = try(statement.Condition, {})
        Keys      = sort(keys(statement))
      }
    }
  }

  test_bootstrap_existing = {
    for statement in jsondecode(data.aws_iam_policy_document.tf_bootstrap.json).Statement :
    statement.Sid => statement
    if !contains(
      ["ManageFoundationRosaIamPolicy", "AttachFoundationRosaIamPolicy"],
      statement.Sid
    )
  }

  test_inline_policies = concat(
    values(aws_iam_role_policy.tf_backend),
    [
      aws_iam_role_policy.tf_bootstrap,
      aws_iam_role_policy.tf_foundation_registry_ci,
      aws_iam_role_policy.tf_foundation_data,
    ],
    aws_iam_role_policy.tf_rosa_first_plan_read,
    aws_iam_role_policy.tf_rosa_cluster_iam,
    aws_iam_role_policy.tf_rosa_data_connection,
  )

  test_inline_totals = {
    for key, role in aws_iam_role.tf : key => sum([
      for policy in local.test_inline_policies :
      policy.role == role.id ? length(jsonencode(jsondecode(policy.policy))) : 0
    ])
  }
}

variable "test_expected_documents" {
  type = any
}

output "test_new_documents" {
  value = local.test_normalized_documents
}

output "test_existing" {
  value = {
    bootstrap = local.test_bootstrap_existing
    trust     = jsondecode(data.aws_iam_policy_document.tf_trust.json)
    backend = {
      for key, policy in aws_iam_role_policy.tf_backend :
      key => {
        name = policy.name
        role = policy.role
        json = jsondecode(policy.policy)
      }
    }
    registry = {
      name = aws_iam_role_policy.tf_foundation_registry_ci.name
      role = aws_iam_role_policy.tf_foundation_registry_ci.role
      json = jsondecode(aws_iam_role_policy.tf_foundation_registry_ci.policy)
    }
    data = {
      name = aws_iam_role_policy.tf_foundation_data.name
      role = aws_iam_role_policy.tf_foundation_data.role
      json = jsondecode(aws_iam_role_policy.tf_foundation_data.policy)
    }
    network = {
      name       = aws_iam_policy.tf_foundation_network.name
      json       = jsondecode(aws_iam_policy.tf_foundation_network.policy)
      role       = aws_iam_role_policy_attachment.tf_foundation_network.role
      policy_arn = aws_iam_role_policy_attachment.tf_foundation_network.policy_arn
    }
    ci_boundary     = jsondecode(aws_iam_policy.ci_boundary.policy)
    backup_boundary = jsondecode(aws_iam_policy.backup_boundary.policy)
  }
}

output "test_sizes" {
  value = {
    foundation_managed = length(jsonencode(jsondecode(
      data.aws_iam_policy_document.tf_foundation_rosa_iam.json
    )))
    inline_totals = local.test_inline_totals
  }
}
output "test_cluster_document" {
  value = try(data.aws_iam_policy_document.tf_rosa_cluster_iam[0].json, null)
}
output "test_data_document" {
  value = try(data.aws_iam_policy_document.tf_rosa_data_connection[0].json, null)
}
'''


def quote(value):
    return json.dumps(value, ensure_ascii=False)


def assertion(condition, message):
    return (
        "  assert {\n"
        f"    condition = {condition}\n"
        f"    error_message = {quote(message)}\n"
        "  }\n"
    )


def hcl_override(kind, target, values):
    text = (
        f"{kind} {{\n"
        "  override_during = apply\n"
        f"  target = {target}\n"
        "  values = {\n"
    )
    for key, value in values.items():
        text += f"    {key} = {quote(value)}\n"
    return text + "  }\n}\n"


def request_match(
    document, action, resource, attached_policy=None, boundary=False
):
    conditions = [
        'statement.Effect == "Allow"',
        "contains(try(tolist(statement.Action), "
        f"[tostring(statement.Action)]), {quote(action)})",
        "contains(try(tolist(statement.Resource), "
        f"[tostring(statement.Resource)]), {quote(resource)})",
    ]
    if attached_policy is not None:
        conditions.append(
            "try(contains("
            'try(tolist(statement.Condition.ArnEquals["iam:PolicyARN"]), '
            '[tostring(statement.Condition.ArnEquals["iam:PolicyARN"])]), '
            + quote(attached_policy)
            + "), true)"
        )
    if boundary:
        conditions.append(
            'try(statement.Condition.Null["iam:PermissionsBoundary"] != "true", true)'
        )
    else:
        conditions.append(
            'try(statement.Condition.Null["iam:PermissionsBoundary"] != "false", true)'
        )
    return (
        "anytrue([\n"
        f"      for statement in local.test_documents.{document}.Statement :\n"
        "      (" + "\n       && ".join(conditions) + ")\n"
        "    ])"
    )


def annotate_documents(source):
    pattern = r'^(data "aws_iam_policy_document" "[^"]+" \{)[ \t]*$'
    annotated, count = re.subn(
        pattern,
        r"\1\n  provider = aws.offline_documents",
        source,
        flags=re.MULTILINE,
    )
    if count != source.count('data "aws_iam_policy_document"'):
        stop("정책 문서 생성기의 시험 연결 위치를 확인해야 합니다.")
    return annotated


with tempfile.TemporaryDirectory(
    prefix="seokpan-rosa-bootstrap-test.", dir="/dev/shm"
) as temporary:
    root = Path(temporary)
    work = root / "configuration"
    work.mkdir()
    tests = work / "tests"
    tests.mkdir()

    (work / "iam.tf").write_text(
        annotate_documents(iam), encoding="utf-8"
    )
    (work / "rosa_iam_execution.tf").write_text(
        annotate_documents(execution), encoding="utf-8"
    )
    (work / "rosa_plan_read_permissions.tf").write_text(
        annotate_documents(read_source), encoding="utf-8"
    )
    (work / "rosa_cluster_iam_execution.tf").write_text(
        annotate_documents(cluster_source), encoding="utf-8"
    )
    (work / "rosa_data_connection_permissions.tf").write_text(
        annotate_documents(data_source), encoding="utf-8"
    )
    (work / "fixtures.tf").write_text(fixtures, encoding="utf-8")
    (work / "versions.tf").write_text(
        'terraform {\n  required_version = "1.16.4"\n'
        '  required_providers {\n    aws = {\n'
        '      source = "hashicorp/aws"\n'
        '      version = "6.67.0"\n'
        '    }\n  }\n}\n',
        encoding="utf-8",
    )
    shutil.copy2(lock, work / ".terraform.lock.hcl")

    mirror = root / "provider-mirror"
    mirror_target = (
        mirror / "registry.terraform.io/hashicorp/aws/6.67.0" / platform
    )
    mirror_target.parent.mkdir(parents=True)
    mirror_target.symlink_to(
        provider_folder.resolve(), target_is_directory=True
    )

    configuration = root / "terraform.rc"
    configuration.write_text(
        'provider_installation {\n  filesystem_mirror {\n'
        f'    path = {quote(str(mirror))}\n'
        '    include = ["registry.terraform.io/hashicorp/aws"]\n'
        '  }\n}\n',
        encoding="utf-8",
    )
    env["TF_CLI_CONFIG_FILE"] = str(configuration)

    (work / "test.tfvars.json").write_text(
        json.dumps({
            "test_expected_documents": {
                "foundation": expected_foundation,
                "bootstrap": expected_bootstrap,
            },
        }),
        encoding="utf-8",
    )

    test = 'mock_provider "aws" {\n  override_during = apply\n}\n'
    test += hcl_override(
        "override_data", "data.aws_caller_identity.current", {
            "account_id": account,
            "arn": f"arn:aws:iam::{account}:user/test",
            "user_id": "TEST",
        }
    )
    test += hcl_override(
        "override_resource", "aws_s3_bucket.tfstate", {
            "arn": "arn:aws:s3:::seokpan-test-state",
        }
    )
    for key in ("bootstrap", "foundation", "rosa"):
        test += hcl_override(
            "override_resource",
            f"aws_iam_role.tf[{quote(key)}]",
            {
                "id": "seokpan-tf-" + key,
                "arn": f"arn:aws:iam::{account}:role/seokpan-tf-{key}",
            },
        )

    for resource, name in [
        ("aws_iam_policy.ci_boundary", "seokpan-fnd-ci-boundary"),
        ("aws_iam_policy.backup_boundary", "seokpan-fnd-backup-boundary"),
        (
            "aws_iam_policy.tf_foundation_network",
            "seokpan-tf-foundation-network",
        ),
        (
            "aws_iam_policy.tf_foundation_rosa_iam[0]",
            "seokpan-tf-foundation-rosa-iam",
        ),
    ]:
        test += hcl_override(
            "override_resource", resource, {
                "arn": f"arn:aws:iam::{account}:policy/{name}",
            }
        )

    test += (
        'run "enabled" {\n'
        '  command = apply\n'
        '  variables {\n'
        '    enable_rosa_account_iam_permissions = true\n'
        '    enable_rosa_plan_read_permissions = true\n'
        '    enable_rosa_cluster_iam_permissions = true\n'
        '    enable_rosa_data_connection_permissions = true\n'
        '    rosa_data_security_group_ids = { rds = "sg-0123456789abcdef0", redis = "sg-0123456789abcdef1" }\n'
        '    rosa_cluster_operator_policy_arns = ' + json.dumps(cluster_test_policies) + '\n'
        '    rosa_plan_foundation_vpc_id = "vpc-0123456789abcdef0"\n'
        '  }\n'
    )
    test += assertion(
        "length(aws_iam_policy.tf_foundation_rosa_iam) == 1"
        " && length(aws_iam_role_policy_attachment.tf_foundation_rosa_iam) == 1"
        ' && aws_iam_policy.tf_foundation_rosa_iam[0].name == "seokpan-tf-foundation-rosa-iam"'
        ' && aws_iam_role_policy_attachment.tf_foundation_rosa_iam[0].role == "seokpan-tf-foundation"'
        " && aws_iam_role_policy_attachment.tf_foundation_rosa_iam[0].policy_arn == "
        + quote(management_arn)
        + " && jsondecode(aws_iam_policy.tf_foundation_rosa_iam[0].policy) == local.test_documents.foundation",
        "관리 정책·연결의 수와 대상이 다릅니다.",
    )
    test += assertion(
        "jsonencode(output.test_new_documents) == jsonencode(var.test_expected_documents)",
        "생성된 정책 문서의 작업·대상·조건이 승인한 범위와 다릅니다.",
    )

    test += assertion(
        "length(aws_iam_role_policy.tf_rosa_first_plan_read) == 1"
        ' && aws_iam_role_policy.tf_rosa_first_plan_read[0].role == "seokpan-tf-rosa"',
        "활성 상태의 조회 정책 개수 또는 연결 역할이 다릅니다.",
    )

    bootstrap_sids = [
        "ManageStateBucket",
        "ManageTfExecRoles",
        "ManageCiBoundaryPolicy",
        "ManageFoundationNetworkPolicy",
        "AttachFoundationNetworkPolicy",
    ]
    active_sids = bootstrap_sids + list(expected_bootstrap)

    test += assertion(
        "toset([for statement in jsondecode(data.aws_iam_policy_document.tf_bootstrap.json).Statement : statement.Sid])"
        " == toset(" + json.dumps(active_sids) + ")",
        "활성 정책 문서에 기존 권한 또는 새 조건부 문서가 누락됐습니다.",
    )
    test += assertion(
        "output.test_sizes.foundation_managed <= 6144"
        " && alltrue([for size in values(output.test_sizes.inline_totals) : size <= 10240])",
        "관리형 정책 또는 역할별 개별 정책 합계가 크기 제한을 초과합니다.",
    )

    cases = [
        (
            "foundation", "iam:CreateRole",
            f"arn:aws:iam::{account}:role/other-role", None, False,
        ),
        (
            "foundation", "iam:CreatePolicy",
            f"arn:aws:iam::{account}:policy/other-policy", None, False,
        ),
        (
            "foundation", "iam:CreateRole",
            f"arn:aws:iam::{account}:role/team/{role_names['installer']}",
            None, False,
        ),
        (
            "foundation", "iam:AttachRolePolicy",
            role_arns["installer"], account_policy_arns["support"], False,
        ),
        (
            "foundation", "iam:DetachRolePolicy",
            role_arns["installer"], account_policy_arns["support"], False,
        ),
        (
            "foundation", "iam:CreateRole",
            role_arns["installer"], None, True,
        ),
        (
            "foundation", "iam:PassRole",
            role_arns["installer"], None, False,
        ),
        (
            "bootstrap", "iam:CreatePolicy",
            f"arn:aws:iam::{account}:policy/other-policy", None, False,
        ),
        (
            "bootstrap", "iam:AttachRolePolicy",
            f"arn:aws:iam::{account}:role/seokpan-tf-rosa",
            management_arn, False,
        ),
        (
            "bootstrap", "iam:AttachRolePolicy",
            foundation_role, account_policy_arns["installer"], False,
        ),
    ]

    for number, values in enumerate(cases, 1):
        document, action, resource, attached, boundary = values
        test += assertion(
            "!(" + request_match(
                document, action, resource, attached, boundary
            ) + ")",
            f"지원 범위 밖 요청이 새 정책에서 허용됩니다: 차단 시험 {number}",
        )

    for key in role_names:
        test += assertion(
            request_match(
                "foundation",
                "iam:AttachRolePolicy",
                role_arns[key],
                account_policy_arns[key],
            ),
            f"승인한 역할과 짝 정책의 연결이 허용되지 않습니다: {key}",
        )
    test += assertion(
        "length(aws_iam_role_policy.tf_rosa_cluster_iam) == 1"
        " && length(aws_iam_role_policy.tf_rosa_data_connection) == 1"
        ' && aws_iam_role_policy.tf_rosa_data_connection[0].name == "seokpan-tf-rosa-data-connect"'
        ' && aws_iam_role_policy.tf_rosa_data_connection[0].role == "seokpan-tf-rosa"'
        ' && aws_iam_role_policy.tf_rosa_cluster_iam[0].name == "seokpan-tf-rosa-cluster-iam"'
        ' && aws_iam_role_policy.tf_rosa_cluster_iam[0].role == "seokpan-tf-rosa"',
        "새 설치권한 정책의 개수 또는 대상이 다릅니다.",
    )
    test += "}\n"

    test += (
        'run "disabled" {\n'
        '  command = apply\n'
        '  variables {\n'
        '    enable_rosa_account_iam_permissions = false\n'
        '    enable_rosa_plan_read_permissions = false\n'
        '    enable_rosa_cluster_iam_permissions = false\n'
        '    enable_rosa_data_connection_permissions = false\n'
        '  }\n'
    )
    test += assertion(
        "length(aws_iam_policy.tf_foundation_rosa_iam) == 0"
        " && length(aws_iam_role_policy_attachment.tf_foundation_rosa_iam) == 0",
        "비활성 설정에 새 관리 정책 또는 연결이 남았습니다.",
    )
    test += assertion(
        "toset([for statement in jsondecode(data.aws_iam_policy_document.tf_bootstrap.json).Statement : statement.Sid])"
        " == toset(" + json.dumps(bootstrap_sids) + ")",
        "비활성 설정의 기존 권한이 달라졌습니다.",
    )
    test += assertion(
        "jsonencode(output.test_existing) == jsonencode(run.enabled.test_existing)",
        "활성화 전후 기존 저장소·네트워크·데이터·자동화 권한 또는 연결이 달라졌습니다.",
    )
    test += assertion(
        "alltrue([for size in values(output.test_sizes.inline_totals) : size <= 10240])",
        "비활성 설정에서 역할별 개별 정책 합계가 크기 제한을 초과합니다.",
    )
    test += "}\n"

    test = test[:-2]
    test += assertion(
        "length(aws_iam_role_policy.tf_rosa_first_plan_read) == 0",
        "비활성 상태에서 조회 정책이 남았습니다.",
    )
    test += assertion(
        "run.enabled.test_sizes.inline_totals.rosa"
        " > output.test_sizes.inline_totals.rosa",
        "조회 정책이 ROSA 역할의 전체 정책 합계에 반영되지 않았습니다.",
    )
    test += "}\n"

    test = test[:-2]
    test += assertion(
        "length(aws_iam_role_policy.tf_rosa_cluster_iam) == 0"
        " && length(aws_iam_role_policy.tf_rosa_data_connection) == 0",
        "비활성 상태에서 설치권한 정책이 남았습니다.",
    )
    test += "}\n"

    (tests / "bootstrap_permissions.tftest.hcl").write_text(
        test, encoding="utf-8"
    )

    print("1. 기존 Provider와 잠금 파일로 초기화합니다.", flush=True)
    result = invoke([
        "terraform", "init",
        "-backend=false", "-get=false",
        "-lockfile=readonly", "-input=false", "-no-color",
    ], work, env)
    if result.returncode:
        print(
            (result.stdout + result.stderr)
            .replace(str(root), "[시험 임시 폴더]")[-6000:]
        )
        stop("격리 시험 초기화가 실패했습니다. 새 설치는 시도하지 않았습니다.")

    print("2. 활성·비활성 및 구조적 차단 시험을 실행합니다.", flush=True)
    result = invoke([
        "terraform", "test",
        "-var-file=test.tfvars.json", "-json", "-verbose",
    ], work, env)

    events = []
    for line in result.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    if result.returncode:
        for event in events:
            diagnostic = event.get("diagnostic")
            if diagnostic:
                print("검사 오류:", diagnostic.get("summary", "확인 필요"))
                print(diagnostic.get("detail", "")[:1600])
                location = diagnostic.get("range") or {}
                if location:
                    print(
                        "검사 위치:",
                        Path(location.get("filename", "")).name,
                        "줄 번호:",
                        (location.get("start") or {}).get("line"),
                    )
        if result.stderr.strip():
            print(result.stderr[-1500:])
        stop("모의시험이 실패했습니다. 커밋·전송하지 않습니다.")

    summaries = [
        event["test_summary"]
        for event in events
        if "test_summary" in event
    ]
    if (
        not summaries
        or summaries[-1].get("passed") != 2
        or summaries[-1].get("failed", 0) != 0
        or summaries[-1].get("skipped", 0) != 0
    ):
        stop("시험 두 개의 통과 결과를 확인하지 못했습니다.")

    def find_sizes(value):
        if isinstance(value, dict):
            candidate = value.get("test_sizes")
            if isinstance(candidate, dict):
                size = candidate.get("value")
                if (
                    isinstance(size, dict)
                    and isinstance(
                        size.get("foundation_managed"), (int, float)
                    )
                ):
                    yield size
            for child in value.values():
                yield from find_sizes(child)
        elif isinstance(value, list):
            for child in value:
                yield from find_sizes(child)

    sizes = []
    for event in events:
        sizes.extend(find_sizes(event))
    if sizes:
        print(
            "추가 관리형 정책의 축약 문서 문자 수:",
            sizes[0]["foundation_managed"],
        )
        for key, size in sizes[0]["inline_totals"].items():
            print("활성 상태 역할별 개별 정책 합계:", key, size)

    print("통과: 활성 시 새 관리 정책 1개·연결 1개 및 Foundation 대상")
    print("통과: 비활성 시 새 관리 정책·연결 없음")
    print("통과: 기존 저장소·네트워크·데이터·자동화 권한과 연결 유지")
    print("통과: 역할 4개·정책 10개의 정확한 대상 및 짝 정책 조건")
    print("통과: 권한 경계 미지정 역할 생성 조건")
    print("통과: 관리형 정책 6144자 및 역할별 개별 정책 합계 10240자 제한")
    print("통과: 지원 범위 밖 요청의 구조적 차단 10개")
    print("통과: 선택적 조회 정책의 활성·비활성 및 합계 반영")
    print("통과: 활성·비활성 모의시험 2개")
    print("확인: 계정·ARN·버킷은 시험용 값입니다.")

print("완료: 임시 구성·시험용 상태 파일·로그 자동 정리")
print("확인: 실제 AWS 권한과 권한 경계·조직 정책의 성공을 뜻하지 않습니다.")
print("미실행: 실제 Backend·AWS API 접속·실제 Foundation Plan·Apply")


def find_cluster_documents(value):
    if isinstance(value, dict):
        entry = value.get("test_cluster_document")
        if isinstance(entry, dict) and isinstance(entry.get("value"), str):
            yield json.loads(entry["value"])
        for child in value.values():
            yield from find_cluster_documents(child)
    elif isinstance(value, list):
        for child in value:
            yield from find_cluster_documents(child)

documents = []
for event in events:
    documents.extend(find_cluster_documents(event))

if not documents or not sizes:
    stop("새 정책 문서 또는 전체 합계 결과를 읽지 못했습니다.")

new_document = documents[0]
creation_tag_allowed = check_cluster_scope(
    new_document, account, operator_names
)

result_path = os.environ.get("SEOKPAN_CLUSTER_REVIEW_RESULT")

if result_path:
    Path(result_path).write_text(
        json.dumps(
            {
                "기존 모의시험": "활성·비활성 2개 통과",
                "새 정책 문자 수": len(json.dumps(
                    new_document, separators=(",", ":")
                )),
                "활성 상태 역할별 전체 정책 합계":
                    sizes[0]["inline_totals"],
                "태그 포함 생성 맥락의 태그 부착 허용":
                    creation_tag_allowed,
                "PR 게시 준비 완료": creation_tag_allowed,
                "확인 범위":
                    "오프라인 검사이며 실제 AWS 생성 성공은 별도 검증",
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    os.chmod(result_path, 0o600)

if not creation_tag_allowed:
    print("확인 필요: 생성 요청 태그만 있는 경우 태그 부착이 허용되지 않습니다.")
    print("PR 게시 보류: 실제 생성 시 태그 권한 조건을 확정해야 합니다.")


# Data 연결 정책도 기존 실제 문서 생성 결과로 검사합니다.
def find_data_documents(value):
    if isinstance(value, dict):
        entry = value.get("test_data_document")
        if isinstance(entry, dict) and isinstance(entry.get("value"), str):
            yield json.loads(entry["value"])
        for child in value.values():
            yield from find_data_documents(child)
    elif isinstance(value, list):
        for child in value:
            yield from find_data_documents(child)


data_documents = []
for event in events:
    data_documents.extend(find_data_documents(event))

if not data_documents:
    stop("새 Data 정책 문서를 읽지 못했습니다.")

data_document = data_documents[0]
data_statements = data_document.get("Statement", [])

expected_data_actions = {
    "ec2:DescribeSecurityGroups",
    "ec2:DescribeSecurityGroupRules",
    "ec2:AuthorizeSecurityGroupIngress",
    "ec2:RevokeSecurityGroupIngress",
    "ec2:CreateTags",
}


def as_list(value):
    return value if isinstance(value, list) else [value]


actual_data_actions = {
    action
    for statement in data_statements
    for action in as_list(statement.get("Action", []))
}

if actual_data_actions != expected_data_actions:
    stop("Data 정책의 작업 목록이 요청 범위와 다릅니다.")

for statement in data_statements:
    if set(statement) - {"Sid", "Effect", "Action", "Resource", "Condition"}:
        stop("검사하지 못하는 정책 문법이 있습니다.")


def data_allowed(action, resource, context):
    context = {key.lower(): value for key, value in context.items()}
    effects = []

    for statement in data_statements:
        if not any(
            fnmatch.fnmatchcase(action.lower(), item.lower())
            for item in as_list(statement.get("Action", []))
        ):
            continue

        if not any(
            fnmatch.fnmatchcase(resource, item)
            for item in as_list(statement.get("Resource", []))
        ):
            continue

        matches = True
        for operator, entries in statement.get("Condition", {}).items():
            if operator != "StringEquals":
                stop("Data 정책의 조건 검사 보완이 필요합니다.")
            for key, expected in entries.items():
                if context.get(key.lower()) not in as_list(expected):
                    matches = False

        if matches:
            effects.append(statement.get("Effect"))

    return "Allow" in effects and "Deny" not in effects


region_context = {"aws:RequestedRegion": "ap-northeast-2"}
tag_context = {
    **region_context,
    "aws:RequestTag/Project": "seokpan",
    "aws:RequestTag/Phase": "2",
    "aws:RequestTag/Component": "rosa",
}
create_context = {
    **tag_context,
    "ec2:CreateAction": "AuthorizeSecurityGroupIngress",
}
sg_arns = [
    "arn:aws:ec2:ap-northeast-2:" + account + ":security-group/" + value
    for value in ("sg-0123456789abcdef0", "sg-0123456789abcdef1")
]
rule_arn = (
    "arn:aws:ec2:ap-northeast-2:" + account
    + ":security-group-rule/sgr-0123456789abcdef0"
)
other_sg = (
    "arn:aws:ec2:ap-northeast-2:" + account
    + ":security-group/sg-0123456789abcdef2"
)
other_account = "111111111111" if account != "111111111111" else "222222222222"
data_cases = []


def data_case(label, expected, action, resource, context):
    data_cases.append(
        (label, expected, data_allowed(action, resource, context))
    )


def tagged_creation_allowed(sg, context):
    # 태그 포함 생성은 대상 보안 그룹·새 규칙·생성 시 태그 권한을 함께 평가합니다.
    return (
        data_allowed("ec2:AuthorizeSecurityGroupIngress", sg, context)
        and data_allowed("ec2:AuthorizeSecurityGroupIngress", rule_arn, context)
        and data_allowed("ec2:CreateTags", rule_arn, context)
    )


for action in ("ec2:DescribeSecurityGroups", "ec2:DescribeSecurityGroupRules"):
    data_case("서울 조회 허용", True, action, "*", region_context)
    data_case(
        "다른 리전 조회 차단", False, action, "*",
        {"aws:RequestedRegion": "us-east-1"},
    )
    data_case("리전 조건 누락 차단", False, action, "*", {})

for sg in sg_arns:
    for action in (
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:RevokeSecurityGroupIngress",
    ):
        data_case("지정 보안 그룹 관리 허용", True, action, sg, region_context)
        data_case("다른 보안 그룹 관리 차단", False, action, other_sg, region_context)
        data_case(
            "다른 계정 보안 그룹 차단", False, action,
            sg.replace(account, other_account), region_context,
        )
        data_case(
            "다른 리전 보안 그룹 차단", False, action,
            sg.replace("ap-northeast-2", "us-east-1"),
            {"aws:RequestedRegion": "us-east-1"},
        )
        data_case("관리 요청의 리전 누락 차단", False, action, sg, {})

    data_cases.append(
        ("태그 포함 접속 규칙 생성 허용", True,
         tagged_creation_allowed(sg, create_context))
    )

    for key in (
        "aws:RequestTag/Project",
        "aws:RequestTag/Phase",
        "aws:RequestTag/Component",
    ):
        missing = dict(create_context)
        missing.pop(key)
        wrong = dict(create_context)
        wrong[key] = "other"
        data_cases.append(
            ("태그 포함 생성의 필수 태그 누락 차단", False,
             tagged_creation_allowed(sg, missing))
        )
        data_cases.append(
            ("태그 포함 생성의 태그 불일치 차단", False,
             tagged_creation_allowed(sg, wrong))
        )

data_cases.append(
    ("태그가 맞아도 다른 보안 그룹 생성 차단", False,
     tagged_creation_allowed(other_sg, create_context))
)

data_case(
    "규칙 생성 시 태그 허용", True,
    "ec2:CreateTags", rule_arn, create_context,
)
data_case(
    "기존 규칙의 직접 태그 변경 차단", False,
    "ec2:CreateTags", rule_arn, tag_context,
)
data_case(
    "보안 그룹 자체 태그 변경 차단", False,
    "ec2:CreateTags", sg_arns[0], create_context,
)

for operation in ("CreateSecurityGroup", "RunInstances", "AuthorizeSecurityGroupEgress"):
    data_case(
        "다른 생성 작업의 태그 권한 차단", False,
        "ec2:CreateTags", rule_arn,
        {**create_context, "ec2:CreateAction": operation},
    )

for action in (
    "ec2:CreateSecurityGroup",
    "ec2:DeleteSecurityGroup",
    "ec2:AuthorizeSecurityGroupEgress",
    "ec2:RevokeSecurityGroupEgress",
    "ec2:ModifySecurityGroupRules",
    "ec2:DeleteTags",
):
    data_case(
        "요청하지 않은 관리 작업 차단", False,
        action, sg_arns[0], create_context,
    )

for action in ("ec2:AuthorizeSecurityGroupIngress", "ec2:CreateTags"):
    data_case(
        "다른 계정 규칙 대상 차단", False,
        action, rule_arn.replace(account, other_account), create_context,
    )
    data_case(
        "다른 리전 규칙 대상 차단", False,
        action, rule_arn.replace("ap-northeast-2", "us-east-1"),
        {**create_context, "aws:RequestedRegion": "us-east-1"},
    )

data_failures = [
    label
    for label, expected, actual in data_cases
    if expected != actual
]

print("Data 정책의 오프라인 사례:", len(data_cases), "개")
print("Data 정책 예상과 일치:", len(data_cases) - len(data_failures), "개")
print("Data 정책 예상과 다름:", len(data_failures), "개")

if data_failures:
    stop("Data 정책의 허용·차단 결과가 요청 범위와 다릅니다.")

print("통과: 지정 보안 그룹 두 개·다른 대상 차단")
print("통과: 태그 포함 생성의 보안 그룹·규칙·태그 권한 대응")
print("통과: 기존 규칙 직접 태그 변경·다른 작업 차단")
print("확인: 태그 없는 생성과 태그 포함 생성은 AWS 평가 범위가 다릅니다.")
print("확인: 실제 Worker 보안 그룹과 실제 AWS 생성 시험은 사용하지 않았습니다.")

if result_path:
    report = json.loads(Path(result_path).read_text(encoding="utf-8"))
    report["Data 정책 오프라인 사례 수"] = len(data_cases)
    report["Data 정책 예상과 다른 사례 수"] = len(data_failures)
    report["Data 정책 문자 수"] = len(
        json.dumps(data_document, separators=(",", ":"))
    )
    report["Data 정책 포함 전체 합계 검사"] = "통과"
    report["PR 게시 준비 완료"] = bool(creation_tag_allowed) and not data_failures

    Path(result_path).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(result_path, 0o600)

print("완료: Data 연결 정책 포함 전체 모의검사")
