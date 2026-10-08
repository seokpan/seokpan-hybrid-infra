#!/usr/bin/env python3
# 작성자: 이유빈
# 작성일: 2026-10-08
# 작성내용: ROSA Operator 정책 태그 및 기존 IAM 구성 격리 모의시험 (#47)
#
# 기존 Provider만 사용하며 AWS·실제 Backend에 접속하지 않습니다.
# 정책 묶음의 실제 위치와 구성표 확인값은 실행 인자로 받습니다.

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser(
    description="ROSA Operator 정책 태그 격리 모의시험"
)
parser.add_argument("--bundle", required=True)
parser.add_argument("--manifest-sha256", required=True)
args = parser.parse_args()

repo = Path(__file__).resolve().parents[1]
foundation = repo / "terraform/foundation"
bundle = Path(args.bundle).resolve()

# 시험 기대값은 구현의 대응표를 참조하지 않고 별도로 정의합니다.
# 근거: 공식 terraform-rhcs-rosa-classic v1.7.2
# modules/operator-policies/main.tf
operators = [
    (
        "seokpan-fnd-rosa-openshift-cloud-credential-operator-cloud-crede",
        "openshift_cloud_credential_operator_cloud_credential_operator_iam_ro_creds_policy.json",
        "openshift-cloud-credential-operator",
        "cloud-credential-operator-iam-ro-creds",
    ),
    (
        "seokpan-fnd-rosa-openshift-cloud-network-config-controller-cloud",
        "openshift_cloud_network_config_controller_cloud_credentials_policy.json",
        "openshift-cloud-network-config-controller",
        "cloud-credentials",
    ),
    (
        "seokpan-fnd-rosa-openshift-cluster-csi-drivers-ebs-cloud-credent",
        "openshift_cluster_csi_drivers_ebs_cloud_credentials_policy.json",
        "openshift-cluster-csi-drivers",
        "ebs-cloud-credentials",
    ),
    (
        "seokpan-fnd-rosa-openshift-image-registry-installer-cloud-creden",
        "openshift_image_registry_installer_cloud_credentials_policy.json",
        "openshift-image-registry",
        "installer-cloud-credentials",
    ),
    (
        "seokpan-fnd-rosa-openshift-ingress-operator-cloud-credentials",
        "openshift_ingress_operator_cloud_credentials_policy.json",
        "openshift-ingress-operator",
        "cloud-credentials",
    ),
    (
        "seokpan-fnd-rosa-openshift-machine-api-aws-cloud-credentials",
        "openshift_machine_api_aws_cloud_credentials_policy.json",
        "openshift-machine-api",
        "aws-cloud-credentials",
    ),
]

roles = {
    "installer": ("Installer", "sts_installer_permission_policy.json"),
    "support": ("Support", "sts_support_permission_policy.json"),
    "controlplane": (
        "ControlPlane", "sts_instance_controlplane_permission_policy.json"
    ),
    "worker": ("Worker", "sts_instance_worker_permission_policy.json"),
}

manifest_path = bundle / "manifest.json"

if not manifest_path.is_file():
    raise SystemExit("중단: 정책 묶음의 구성표가 없습니다.")

if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != args.manifest_sha256:
    raise SystemExit("중단: 구성표 확인값이 기존 기록과 다릅니다.")

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
registered = manifest.get("files_sha256", {})

if len(registered) != 17:
    raise SystemExit("중단: 구성표 등록 파일 수가 예상과 다릅니다.")

for filename, expected in registered.items():
    if Path(filename).name != filename:
        raise SystemExit("중단: 구성표에 예상하지 않은 파일 경로가 있습니다.")
    path = bundle / filename
    if path.is_symlink() or not path.is_file():
        raise SystemExit("중단: 등록 정책 파일을 확인해야 합니다.")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit("중단: 등록 정책 파일의 확인값이 다릅니다.")

env = {
    key: value for key, value in os.environ.items()
    if not key.startswith(("AWS_", "TF_"))
}
env["CHECKPOINT_DISABLE"] = "1"
env["TF_IN_AUTOMATION"] = "1"

version_result = subprocess.run(
    ["terraform", "version", "-json"],
    cwd=foundation, env=env, capture_output=True, text=True, check=True,
)
version = json.loads(version_result.stdout)

if version["terraform_version"] != "1.16.4":
    raise SystemExit("중단: 기존 Terraform 1.16.4가 필요합니다.")

platform = version["platform"]
provider_folder = (
    foundation / ".terraform/providers/registry.terraform.io/hashicorp/aws"
    / "6.67.0" / platform
)
binaries = [
    path for path in provider_folder.glob("terraform-provider-aws*")
    if path.is_file() and os.access(path, os.X_OK)
]

if len(binaries) != 1:
    raise SystemExit("중단: 기존 AWS Provider 6.67.0 실행 파일을 확인해야 합니다.")

lock = foundation / ".terraform.lock.hcl"
if not lock.is_file():
    raise SystemExit("중단: 기존 Provider 잠금 파일이 없습니다.")

files = [
    "rosa_iam_variables.tf",
    "rosa_iam_policy_inputs.tf",
    "rosa_iam_policy_catalog.tf",
    "rosa_iam_policy_bundle.tf",
    "rosa_iam_metadata_variables.tf",
    "rosa_iam_policies.tf",
    "rosa_iam_role_inputs.tf",
    "rosa_iam_roles.tf",
    "rosa_iam_outputs.tf",
]

for filename in files:
    if not (foundation / filename).is_file():
        raise SystemExit("중단: 시험에 필요한 기존 ROSA 코드가 없습니다.")

def quote(value):
    return json.dumps(value, ensure_ascii=False)

def assertion(condition, message):
    return (
        "  assert {\n"
        f"    condition = {condition}\n"
        f"    error_message = {quote(message)}\n"
        "  }\n"
    )

with tempfile.TemporaryDirectory(
    prefix="seokpan-rosa-tag-test.", dir="/dev/shm"
) as temporary:
    root = Path(temporary)
    work = root / "configuration"
    work.mkdir()
    tests = work / "tests"
    tests.mkdir()

    for filename in files:
        shutil.copy2(foundation / filename, work / filename)
    shutil.copy2(lock, work / ".terraform.lock.hcl")

    policy_copy = root / "policies"
    policy_copy.mkdir()
    for filename in ["manifest.json", *registered]:
        shutil.copy2(bundle / filename, policy_copy / filename)

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
        encoding="utf-8",
    )

    mirror = root / "provider-mirror"
    mirror_target = (
        mirror / "registry.terraform.io/hashicorp/aws/6.67.0" / platform
    )
    # 기존 Provider의 실행 파일과 부속 파일을 함께 유지합니다.
    # 원본 폴더는 수정하거나 새로 설치하지 않습니다.
    mirror_target.parent.mkdir(parents=True)
    mirror_target.symlink_to(
        provider_folder.resolve(),
        target_is_directory=True,
    )

    # 네트워크 설치 경로를 두지 않습니다.
    configuration = root / "terraform.rc"
    configuration.write_text(
        'provider_installation {\n'
        '  filesystem_mirror {\n'
        f'    path = {quote(str(mirror))}\n'
        '    include = ["registry.terraform.io/hashicorp/aws"]\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )
    env["TF_CLI_CONFIG_FILE"] = str(configuration)

    (work / "test.tfvars.json").write_text(
        json.dumps({
            "enable_rosa_account_iam": True,
            "rosa_policy_bundle_directory": str(policy_copy),
            "rosa_policy_bundle_manifest_sha256": args.manifest_sha256,
            "rosa_iam_openshift_minor_version": "0.0",
        }),
        encoding="utf-8",
    )

    # 시험용 값을 Plan 단계부터 공급합니다.
    test = (
        'mock_provider "aws" {\n'
        '  override_during = plan\n'
        '}\n\n'
    )

    # 서로 다른 시험용 ARN을 사용해 역할별 정책 연결도 검사합니다.
    for key in roles:
        for resource, kind in [
            ("aws_iam_role.rosa_account", "role"),
            ("aws_iam_policy.rosa_account", "policy"),
        ]:
            test += (
                "override_resource {\n"
                "  override_during = plan\n"
                f"  target = {resource}[{quote(key)}]\n"
                "  values = {\n"
                f'    arn = "arn:aws:iam::000000000000:{kind}/test-{key}"\n'
                "  }\n"
                "}\n\n"
            )

    test += 'run "active" {\n  command = plan\n'
    test += assertion(
        "length(aws_iam_role.rosa_account) == 4"
        " && length(aws_iam_policy.rosa_account) == 4"
        " && length(aws_iam_policy.rosa_operator) == 6"
        " && length(aws_iam_role_policy_attachment.rosa_account) == 4",
        "역할·정책·연결 수가 예상과 다릅니다.",
    )

    for name, filename, namespace, operator_name in operators:
        ref = f"aws_iam_policy.rosa_operator[{quote(name)}]"
        conditions = [
            f"{ref}.name == {quote(name)}",
            f'{ref}.tags["rosa_openshift_version"] == "0.0"',
            f'{ref}.tags["rosa_role_prefix"] == "seokpan-fnd-rosa"',
            f'{ref}.tags["operator_namespace"] == {quote(namespace)}',
            f'{ref}.tags["operator_name"] == {quote(operator_name)}',
            f"jsondecode({ref}.policy) == "
            f'jsondecode(file("${{var.rosa_policy_bundle_directory}}/{filename}"))',
        ]
        test += assertion(
            "(\n      " + "\n      && ".join(conditions) + "\n    )",
            f"Operator 정책의 이름·공식 권한·식별 태그가 다릅니다: {name}",
        )

    for key, (role_name, filename) in roles.items():
        role = f"aws_iam_role.rosa_account[{quote(key)}]"
        policy = f"aws_iam_policy.rosa_account[{quote(key)}]"
        attachment = f"aws_iam_role_policy_attachment.rosa_account[{quote(key)}]"
        test += assertion(
            "(\n"
            f'      {role}.name == "seokpan-fnd-rosa-{role_name}-Role"\n'
            f'      && {policy}.name == "seokpan-fnd-rosa-{role_name}-Role-Policy"\n'
            f"      && {attachment}.role == {role}.name\n"
            f"      && {attachment}.policy_arn == {policy}.arn\n"
            f"      && jsondecode({policy}.policy) == "
            f'jsondecode(file("${{var.rosa_policy_bundle_directory}}/{filename}"))\n'
            "    )",
            f"공통 역할의 이름·정책·연결이 다릅니다: {key}",
        )

    test += assertion(
        "length(output.rosa_account_role_arns) == 4"
        " && length(output.rosa_operator_policy_arns) == 6",
        "제한 출력의 항목 수가 다릅니다.",
    )
    test += "}\n\n"

    test += (
        'run "inactive" {\n'
        '  command = plan\n'
        '  variables {\n'
        '    enable_rosa_account_iam = false\n'
        '    rosa_policy_bundle_directory = null\n'
        '    rosa_policy_bundle_manifest_sha256 = null\n'
        '    rosa_iam_openshift_minor_version = null\n'
        '  }\n'
    )
    test += assertion(
        "length(aws_iam_role.rosa_account) == 0"
        " && length(aws_iam_policy.rosa_account) == 0"
        " && length(aws_iam_policy.rosa_operator) == 0"
        " && length(aws_iam_role_policy_attachment.rosa_account) == 0"
        " && length(output.rosa_account_role_arns) == 0"
        " && length(output.rosa_operator_policy_arns) == 0",
        "비활성 설정에 IAM 자원 또는 출력이 남았습니다.",
    )
    test += "}\n"
    (tests / "operator_tags.tftest.hcl").write_text(test, encoding="utf-8")

    print("1. 기존 Provider로 격리 시험 구성을 준비합니다.", flush=True)
    initialized = subprocess.run(
        ["terraform", "init", "-backend=false", "-get=false",
         "-lockfile=readonly", "-input=false", "-no-color"],
        cwd=work, env=env, capture_output=True, text=True,
    )
    if initialized.returncode:
        print("초기화 오류 상세 — 도구가 출력한 원문입니다.")
        for output in (initialized.stdout, initialized.stderr):
            if output.strip():
                cleaned = output.replace(
                    str(root), "[시험 임시 폴더]"
                ).replace(str(repo), "[프로젝트 폴더]")
                print(cleaned[-8000:])
        raise SystemExit(
            "중단: 기존 Provider를 이용한 시험 초기화가 실패했습니다. "
            "새 Provider 설치는 시도하지 않았습니다."
        )

    print("2. 활성·비활성 모의시험을 실행합니다.", flush=True)
    result = subprocess.run(
        ["terraform", "test", "-var-file=test.tfvars.json", "-json"],
        cwd=work, env=env, capture_output=True, text=True,
    )
    events = []
    for line in result.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    if result.returncode:
        for event in events:
            diagnostic = event.get("diagnostic", {})
            if diagnostic:
                print("검사 오류:", diagnostic.get("summary", "검사 실패"))
                location = diagnostic.get("range") or {}
                start = location.get("start") or {}
                if location:
                    print(
                        "검사 위치:",
                        Path(location.get("filename", "")).name,
                        "줄 번호:",
                        start.get("line", "확인 필요"),
                    )
        raise SystemExit("중단: 모의시험이 실패했습니다. 커밋·전송하지 않습니다.")

    summaries = [
        event["test_summary"] for event in events
        if "test_summary" in event
    ]
    if not summaries or summaries[-1].get("passed") != 2:
        raise SystemExit("중단: 모의시험 두 개의 통과 결과를 확인하지 못했습니다.")

    print("통과: Operator 정책 6개의 공식 식별 태그 4개")
    print("통과: 기존 정책 이름 및 공식 권한 내용")
    print("통과: 공통 역할 4개·정책 10개·연결 4개")
    print("통과: 역할별 정책 연결 및 제한 출력 항목 수")
    print("통과: 비활성 설정의 IAM 자원과 출력 없음")
    print("확인: 버전 0.0과 시험용 ARN은 실제 공급값이 아닙니다.")

print("완료: 임시 시험 구성·정책 사본·로그 자동 정리")
print("확인: 재실행할 시험 절차 파일은 저장소에 유지합니다.")
print("미실행: AWS 접속·실제 Foundation Plan·Apply")
