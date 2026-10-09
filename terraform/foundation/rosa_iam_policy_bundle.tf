# 작성자: 이유빈
# 작성일: 2026-10-08
# 작성내용: ROSA 공식 정책 묶음의 입력·해시·필수 파일 검사 (#47)

# 공통 IAM 생성을 활성화했을 때 정책 묶음을 검사합니다.
# 비활성 상태에서는 보호 폴더의 파일을 읽지 않습니다.
# AWS 역할·정책 생성 선언은 후속 파일에서 작성하며,
# 해당 자원이 아래 검사에 의존하도록 연결합니다.

locals {
  rosa_bundle_directory = (
    var.rosa_policy_bundle_directory == null
    ? ""
    : trimsuffix(var.rosa_policy_bundle_directory, "/")
  )

  rosa_bundle_manifest_path = "${local.rosa_bundle_directory}/manifest.json"

  rosa_bundle_manifest = try(
    jsondecode(
      var.enable_rosa_account_iam
      ? file(local.rosa_bundle_manifest_path)
      : "{}"
    ),
    {}
  )

  rosa_bundle_registered_hashes = try(
    tomap(local.rosa_bundle_manifest.files_sha256),
    tomap({})
  )

  rosa_bundle_manifest_hash = (
    var.enable_rosa_account_iam
    ? try(filesha256(local.rosa_bundle_manifest_path), "")
    : ""
  )

  rosa_bundle_required_files = toset(concat(
    values(local.rosa_account_permission_policy_files),
    values(local.rosa_operator_permission_policy_files),
    ["sts_support_trust_policy.json"]
  ))

  rosa_bundle_filenames_valid = alltrue([
    for filename in keys(local.rosa_bundle_registered_hashes) :
    can(regex("^[A-Za-z0-9_-]+\\.json$", filename))
  ])

  rosa_bundle_file_hashes_valid = alltrue([
    for filename, expected_hash in local.rosa_bundle_registered_hashes :
    can(regex("^[A-Za-z0-9_-]+\\.json$", filename))
    ? (
      can(regex("^[0-9a-f]{64}$", expected_hash)) &&
      try(
        filesha256("${local.rosa_bundle_directory}/${filename}"),
        ""
      ) == expected_hash
    )
    : false
  ])

  rosa_bundle_required_files_present = alltrue([
    for filename in local.rosa_bundle_required_files :
    contains(keys(local.rosa_bundle_registered_hashes), filename)
  ])
}

resource "terraform_data" "rosa_policy_bundle_guard" {
  count = var.enable_rosa_account_iam ? 1 : 0

  lifecycle {
    # 현재 bootstrap 실행 권한과 지원 입력 범위를 일치시킵니다.
    # 생성 비활성 상태에서는 이 검사 자원이 만들어지지 않습니다.
    precondition {
      condition     = var.rosa_iam_path == "/"
      error_message = "현재 ROSA 공통 IAM 실행 권한은 경로 /만 지원합니다. 다른 경로를 사용하려면 bootstrap 권한과 함께 별도 검토해야 합니다."
    }

    precondition {
      condition     = var.rosa_account_permissions_boundary == null
      error_message = "현재 ROSA 공통 IAM 실행 권한은 권한 경계 미지정만 지원합니다. 권한 경계가 필요하면 bootstrap 권한과 함께 별도 검토해야 합니다."
    }

    precondition {
      condition = (
        var.rosa_policy_bundle_directory != null &&
        var.rosa_policy_bundle_manifest_sha256 != null &&
        local.rosa_bundle_directory != ""
      )
      error_message = "ROSA 공통 IAM 생성에는 검증한 정책 폴더와 구성표 해시 입력이 필요합니다."
    }

    precondition {
      condition = (
        local.rosa_bundle_manifest_hash ==
        var.rosa_policy_bundle_manifest_sha256
      )
      error_message = "정책 구성표의 해시가 채택한 입력과 다릅니다."
    }

    precondition {
      condition = (
        length(local.rosa_bundle_registered_hashes) == 17 &&
        local.rosa_bundle_filenames_valid
      )
      error_message = "정책 구성표의 등록 파일 수 또는 파일 이름이 기존 수신 기준과 다릅니다."
    }

    precondition {
      condition     = local.rosa_bundle_file_hashes_valid
      error_message = "정책 파일이 없거나 내용의 해시가 구성표와 다릅니다."
    }

    precondition {
      condition     = local.rosa_bundle_required_files_present
      error_message = "역할·Operator 권한 정책 또는 Support 신뢰 정책 파일이 누락됐습니다."
    }

    precondition {
      condition     = var.rosa_account_role_prefix == "seokpan-fnd-rosa"
      error_message = "현재 정책 대응표는 승인한 접두사 seokpan-fnd-rosa에 맞춰져 있습니다. 접두사 변경 전 소비 이름을 다시 대조해야 합니다."
    }
  }
}
