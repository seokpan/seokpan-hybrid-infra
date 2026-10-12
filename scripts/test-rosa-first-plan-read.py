#!/usr/bin/env python3
# 작성자: 이유빈
# 작성일: 2026-10-12
# 작성내용: ROSA 최초 Plan 조회 권한의 격리 모의시험 (#25)

import ast
import hashlib
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

def require(condition, message):
    if not condition:
        raise SystemExit("중단: " + message)

def invoke(args, cwd, environment):
    return subprocess.run(
        args, cwd=cwd, env=environment,
        capture_output=True, text=True
    )

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

environment = {
    key: value for key, value in os.environ.items()
    if not key.startswith(("AWS_", "TF_"))
}
environment.update({
    "CHECKPOINT_DISABLE": "1",
    "TF_IN_AUTOMATION": "1",
    "AWS_EC2_METADATA_DISABLED": "true",
})

result = invoke(
    ["terraform", "version", "-json"], repo, environment
)
require(result.returncode == 0, "Terraform 버전을 확인하지 못했습니다.")
version = json.loads(result.stdout)
require(
    version["terraform_version"] == "1.16.4",
    "기존 Terraform 1.16.4가 필요합니다."
)

provider = (
    foundation / ".terraform/providers/registry.terraform.io"
    / "hashicorp/aws/6.67.0" / version["platform"]
)
require(provider.is_dir(), "기존 AWS Provider 6.67.0이 없습니다.")

helper = repo / "scripts/test-rosa-bootstrap-permissions.py"
require(
    helper.is_file() and not helper.is_symlink(),
    "기존 모의시험 틀을 확인해야 합니다."
)

# 기존 검사 파일을 실행하지 않고 문자열 Fixture만 읽습니다.
tree = ast.parse(helper.read_text(encoding="utf-8"))
fixtures = None
for node in tree.body:
    if isinstance(node, ast.Assign):
        if any(
            isinstance(item, ast.Name) and item.id == "fixtures"
            for item in node.targets
        ):
            fixtures = ast.literal_eval(node.value)

require(isinstance(fixtures, str), "기존 시험용 구성을 찾지 못했습니다.")

require(
    fixtures.count(
        "    aws_iam_role_policy.tf_rosa_first_plan_read,"
    ) == 1,
    "기존 시험 구성에 조회 정책이 정확히 한 번 포함돼야 합니다."
)

fixtures += '''
variable "test_expected_read" {
  type = any
}

output "test_read_document" {
  value = {
    for statement in try(
      jsondecode(aws_iam_role_policy.tf_rosa_first_plan_read[0].policy).Statement,
      []
    ) : statement.Sid => {
      Effect = statement.Effect
      Action = sort(try(tolist(statement.Action), [tostring(statement.Action)]))
      Resource = sort(try(tolist(statement.Resource), [tostring(statement.Resource)]))
      Condition = try(statement.Condition, {})
      Keys = sort(keys(statement))
    }
  }
}
'''

sources = [
    bootstrap / "iam.tf",
    bootstrap / "rosa_iam_execution.tf",
    bootstrap / "rosa_plan_read_permissions.tf",
    bootstrap / ".terraform.lock.hcl",
]
for path in sources:
    require(
        path.is_file() and not path.is_symlink(),
        "검사할 코드 또는 잠금 파일이 없습니다."
    )
before = {path: digest(path) for path in sources}

def annotate(text):
    pattern = r'^(data "aws_iam_policy_document" "[^"]+" \{)[ \t]*$'
    result, count = re.subn(
        pattern,
        r"\1\n  provider = aws.offline_documents",
        text, flags=re.MULTILINE
    )
    require(
        count == text.count('data "aws_iam_policy_document"'),
        "로컬 문서 생성기의 연결을 확인해야 합니다."
    )
    return result

account = "000000000000"
vpc = "vpc-0123456789abcdef0"
region_condition = {
    "StringEquals": {"aws:RequestedRegion": "ap-northeast-2"}
}

def expected(actions, resources, condition=None):
    condition = condition or {}
    keys = ["Sid", "Effect", "Action", "Resource"]
    if condition:
        keys.append("Condition")
    return {
        "Effect": "Allow",
        "Action": sorted(actions),
        "Resource": sorted(resources),
        "Condition": condition,
        "Keys": sorted(keys),
    }

expected_document = {
    "ReadSeoulNetworkLists": expected(
        [
            "ec2:DescribeVpcs",
            "ec2:DescribeSubnets",
            "ec2:DescribeRouteTables",
        ],
        ["*"], region_condition
    ),
    "ReadSuppliedFoundationVpcAttributes": expected(
        ["ec2:DescribeVpcAttribute"],
        [f"arn:aws:ec2:ap-northeast-2:{account}:vpc/{vpc}"],
        region_condition
    ),
    "ReadFoundationAccountRoles": expected(
        ["iam:GetRole"],
        [
            f"arn:aws:iam::{account}:role/seokpan-fnd-rosa-{name}-Role"
            for name in ("Installer", "Support", "ControlPlane", "Worker")
        ]
    ),
}

def override(kind, target, values):
    body = "\n".join(
        f"    {key} = {json.dumps(value)}"
        for key, value in values.items()
    )
    return (
        f"{kind} {{\n"
        "  override_during = apply\n"
        f"  target = {target}\n"
        f"  values = {{\n{body}\n  }}\n"
        "}\n"
    )

with tempfile.TemporaryDirectory(
    prefix="seokpan-rosa-read-test.", dir="/dev/shm"
) as temporary:
    root = Path(temporary)
    work = root / "configuration"
    work.mkdir()
    tests = work / "tests"
    tests.mkdir()

    for path in sources[:-1]:
        (work / path.name).write_text(
            annotate(path.read_text(encoding="utf-8")),
            encoding="utf-8"
        )

    (work / "fixtures.tf").write_text(fixtures, encoding="utf-8")
    (work / "versions.tf").write_text(
        'terraform {\n'
        '  required_version = "1.16.4"\n'
        '  required_providers {\n'
        '    aws = {\n'
        '      source = "hashicorp/aws"\n'
        '      version = "6.67.0"\n'
        '    }\n'
        '  }\n'
        '}\n',
        encoding="utf-8"
    )
    shutil.copy2(sources[-1], work / ".terraform.lock.hcl")

    mirror = root / "mirror"
    destination = (
        mirror / "registry.terraform.io/hashicorp/aws"
        / "6.67.0" / version["platform"]
    )
    destination.parent.mkdir(parents=True)
    destination.symlink_to(provider.resolve(), target_is_directory=True)

    configuration = root / "terraform.rc"
    configuration.write_text(
        'provider_installation {\n'
        '  filesystem_mirror {\n'
        f'    path = {json.dumps(str(mirror))}\n'
        '    include = ["registry.terraform.io/hashicorp/aws"]\n'
        '  }\n'
        '}\n',
        encoding="utf-8"
    )
    environment["TF_CLI_CONFIG_FILE"] = str(configuration)

    (work / "test.tfvars.json").write_text(
        json.dumps({
            "test_expected_documents": {
                "foundation": {}, "bootstrap": {}
            },
            "test_expected_read": expected_document,
            "enable_rosa_account_iam_permissions": True,
        }),
        encoding="utf-8"
    )

    test = 'mock_provider "aws" {\n  override_during = apply\n}\n'
    test += override(
        "override_data", "data.aws_caller_identity.current", {
            "account_id": account,
            "arn": f"arn:aws:iam::{account}:user/test",
            "user_id": "TEST",
        }
    )
    test += override(
        "override_resource", "aws_s3_bucket.tfstate", {
            "arn": "arn:aws:s3:::seokpan-test-state",
        }
    )
    for key in ("bootstrap", "foundation", "rosa"):
        test += override(
            "override_resource", f'aws_iam_role.tf["{key}"]', {
                "id": "seokpan-tf-" + key,
                "name": "seokpan-tf-" + key,
                "arn": f"arn:aws:iam::{account}:role/seokpan-tf-{key}",
            }
        )
    for address, name in (
        ("aws_iam_policy.ci_boundary", "seokpan-fnd-ci-boundary"),
        ("aws_iam_policy.backup_boundary", "seokpan-fnd-backup-boundary"),
        ("aws_iam_policy.tf_foundation_network", "seokpan-tf-foundation-network"),
        ("aws_iam_policy.tf_foundation_rosa_iam[0]", "seokpan-tf-foundation-rosa-iam"),
    ):
        test += override("override_resource", address, {
            "arn": f"arn:aws:iam::{account}:policy/{name}",
        })

    test += '''
run "default_disabled" {
  command = apply
  assert {
    condition = length(aws_iam_role_policy.tf_rosa_first_plan_read) == 0
    error_message = "기본 비활성 상태에서 새 정책이 생성됐습니다."
  }
}

run "enabled_exact_scope" {
  command = apply
  variables {
    enable_rosa_plan_read_permissions = true
    rosa_plan_foundation_vpc_id = "vpc-0123456789abcdef0"
  }
  assert {
    condition = (
      length(aws_iam_role_policy.tf_rosa_first_plan_read) == 1 &&
      aws_iam_role_policy.tf_rosa_first_plan_read[0].role == "seokpan-tf-rosa"
    )
    error_message = "새 정책의 개수 또는 연결 역할이 다릅니다."
  }
  assert {
    condition = jsonencode(output.test_read_document) == jsonencode(var.test_expected_read)
    error_message = "조회 작업·대상·리전 조건이 기대 범위와 다릅니다."
  }
  assert {
    condition = jsonencode(output.test_existing) == jsonencode(run.default_disabled.test_existing)
    error_message = "기존 정책 또는 연결이 변경됐습니다."
  }
  assert {
    condition = alltrue([
      for size in values(output.test_sizes.inline_totals) : size <= 10240
    ])
    error_message = "역할별 개별 정책 합계 크기를 초과합니다."
  }
}

run "missing_vpc_blocked" {
  command = plan
  variables {
    enable_rosa_plan_read_permissions = true
    rosa_plan_foundation_vpc_id = null
  }
  expect_failures = [aws_iam_role_policy.tf_rosa_first_plan_read]
}

run "invalid_vpc_blocked" {
  command = plan
  variables {
    enable_rosa_plan_read_permissions = true
    rosa_plan_foundation_vpc_id = "잘못된입력"
  }
  expect_failures = [var.rosa_plan_foundation_vpc_id]
}
'''
    (tests / "first_plan_read.tftest.hcl").write_text(
        test, encoding="utf-8"
    )

    print("1. 기존 Provider로 격리 시험을 초기화합니다.", flush=True)
    result = invoke([
        "terraform", "init", "-backend=false", "-get=false",
        "-lockfile=readonly", "-input=false", "-no-color",
    ], work, environment)
    require(
        result.returncode == 0,
        "격리 시험 초기화 실패:\n"
        + (result.stdout + result.stderr).replace(str(root), "[시험 폴더]")[-3000:]
    )

    print("2. 활성·비활성 및 입력 차단 시험을 실행합니다.", flush=True)
    result = invoke([
        "terraform", "test", "-var-file=test.tfvars.json", "-json"
    ], work, environment)

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
                print(diagnostic.get("detail", "")[:1200])
        raise SystemExit("중단: 모의시험 실패. 커밋·전송하지 않습니다.")

    summaries = [
        event["test_summary"]
        for event in events if "test_summary" in event
    ]
    require(
        summaries
        and summaries[-1].get("passed") == 4
        and summaries[-1].get("failed", 0) == 0
        and summaries[-1].get("skipped", 0) == 0,
        "시험 4개의 통과 결과를 확인하지 못했습니다."
    )

require(
    all(digest(path) == before[path] for path in sources),
    "시험 중 원본 코드 또는 잠금 파일이 변경됐습니다."
)

print("통과: 기본 비활성 상태에서 새 정책 없음")
print("통과: 활성 시 ROSA 역할에 조회 정책 1개 연결")
print("통과: 조회 작업·공통 역할 4개·지정 VPC·서울 조건 일치")
print("통과: 기존 정책·연결 유지 및 역할별 합계 크기 제한")
print("통과: VPC 입력 누락·잘못된 형식 차단")
print("통과: 모의시험 4개")
print("완료: 임시 구성·시험 상태·로그 정리")
print("확인: 시험용 계정·VPC는 실제 공급값이 아닙니다.")
print("확인: 원본 코드·잠금 파일과 재실행 검사 파일은 유지합니다.")
print("미실행: 실제 Backend·AWS API 접속·실제 Plan·Apply")
