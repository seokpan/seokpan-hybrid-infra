# 작성자: 이유빈
# 작성일: 2026-10-08
# 작성내용: ROSA 역할별 공식 정책 파일과 소비 정책 이름의 대응표 (#47)

# 역할별 파일 이름과 태훈 님이 전달한 정책 이름을 연결합니다.
# 공식 정책의 권한 내용과 소비 정책 이름을 임의로 변경하지 않습니다.
# 실제 정책 파일 읽기와 해시 검증 조건은 후속 구현에서 연결합니다.
# 이 파일에는 AWS 역할·정책 생성 선언이나 실제 ARN이 없습니다.
# 아래 Operator 이름은 승인한 접두사 seokpan-fnd-rosa에 대응합니다.

locals {
  rosa_account_permission_policy_files = {
    "installer"    = "sts_installer_permission_policy.json"
    "support"      = "sts_support_permission_policy.json"
    "controlplane" = "sts_instance_controlplane_permission_policy.json"
    "worker"       = "sts_instance_worker_permission_policy.json"
  }

  rosa_operator_permission_policy_files = {
    "seokpan-fnd-rosa-openshift-cloud-credential-operator-cloud-crede" = "openshift_cloud_credential_operator_cloud_credential_operator_iam_ro_creds_policy.json"
    "seokpan-fnd-rosa-openshift-cloud-network-config-controller-cloud" = "openshift_cloud_network_config_controller_cloud_credentials_policy.json"
    "seokpan-fnd-rosa-openshift-cluster-csi-drivers-ebs-cloud-credent" = "openshift_cluster_csi_drivers_ebs_cloud_credentials_policy.json"
    "seokpan-fnd-rosa-openshift-image-registry-installer-cloud-creden" = "openshift_image_registry_installer_cloud_credentials_policy.json"
    "seokpan-fnd-rosa-openshift-ingress-operator-cloud-credentials"    = "openshift_ingress_operator_cloud_credentials_policy.json"
    "seokpan-fnd-rosa-openshift-machine-api-aws-cloud-credentials"     = "openshift_machine_api_aws_cloud_credentials_policy.json"
  }
}
