#!/bin/bash
# 작성자: 이유빈
# 작성일: 2026-10-07
# 작성내용: AL2023 VPN EC2의 SSM 관리 프로그램 준비 초안 (#16)

set -euo pipefail
umask 077

# EC2 내부에서 실행할 초기 설정입니다.
# WireGuard 개인키와 AWS 인증정보는 포함하지 않습니다.
trap 'echo "오류: VPN 관리 초기 설정 명령이 실패했습니다." >&2' ERR

if [ "$(id -u)" -ne 0 ]; then
    echo '중단: EC2 내부의 root 권한으로 실행해야 합니다.' >&2
    exit 1
fi

source /etc/os-release

if [ "${ID:-}" != "amzn" ] || [ "${VERSION_ID:-}" != "2023" ]; then
    echo '중단: Amazon Linux 2023용 설정입니다.' >&2
    exit 1
fi

if [ "$(uname -m)" != "x86_64" ]; then
    echo '중단: 승인한 x86_64 구성과 다릅니다.' >&2
    exit 1
fi

echo '1. SSM Agent 설치 여부를 확인합니다.'

if rpm -q amazon-ssm-agent >/dev/null 2>&1; then
    echo 'SSM Agent가 이미 설치되어 있습니다.'
else
    # 서울 리전의 AWS 공식 설치 파일을 사용합니다.
    # 초기 부팅 중 네트워크 준비 지연에 대비해 재시도합니다.
    ssm_package_url='https://s3.ap-northeast-2.amazonaws.com/amazon-ssm-ap-northeast-2/latest/linux_amd64/amazon-ssm-agent.rpm'
    ssm_installed=no

    for attempt in 1 2 3 4 5 6; do
        if dnf install -y "$ssm_package_url"; then
            ssm_installed=yes
            break
        fi

        if [ "$attempt" -lt 6 ]; then
            sleep 10
        fi
    done

    if [ "$ssm_installed" != "yes" ]; then
        echo '실패: SSM Agent를 설치하지 못했습니다.' >&2
        exit 1
    fi
fi

echo '2. SSM Agent를 실행하고 부팅 시 자동 실행하도록 설정합니다.'
systemctl enable --now amazon-ssm-agent

echo '3. SSM Agent의 자동 실행 설정과 실행 상태를 확인합니다.'
systemctl is-enabled amazon-ssm-agent
systemctl is-active --quiet amazon-ssm-agent

echo '4. 실제 설치 버전을 기록합니다.'
rpm -q amazon-ssm-agent

echo '성공: SSM Agent 서비스가 실행 중입니다.'
echo '성공: VPN 관리 초기 설정을 완료했습니다.'

# 위 성공 표시는 Agent 서비스 확인입니다.
# 관리자의 SSM 접속이나 WireGuard 연결 성공을 의미하지 않습니다.
# WireGuard·IP 전달·호스트 방화벽·개인키 공급은 후속 설정입니다.
