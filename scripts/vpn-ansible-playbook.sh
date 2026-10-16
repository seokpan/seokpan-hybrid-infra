#!/bin/bash
# 작성자: 이유빈
# 작성일: 2026-10-07
# 작성내용: 기존 Ansible과 사용자 전용 VPN SDK 환경의 실행 진입점 (#16)

set -euo pipefail

vpn_venv="$HOME/.local/share/seokpan-hybrid/venv-vpn"
vpn_collections="$HOME/.local/share/seokpan-hybrid/collections"

if [ ! -x "$vpn_venv/bin/python" ]; then
    echo '중단: VPN용 SDK 환경을 먼저 준비해야 합니다.' >&2
    exit 1
fi

if [ ! -f "$vpn_collections/ansible_collections/community/aws/MANIFEST.json" ]; then
    echo '중단: VPN용 AWS 컬렉션이 없습니다.' >&2
    exit 1
fi

if [ ! -f /usr/bin/ansible-playbook ]; then
    echo '중단: 기존 Ansible 실행 파일이 없습니다.' >&2
    exit 1
fi

if [ "$#" -eq 0 ]; then
    echo '사용법: bash scripts/vpn-ansible-playbook.sh <Ansible 인자>' >&2
    exit 1
fi

export PYTHONNOUSERSITE=1
export ANSIBLE_COLLECTIONS_PATHS="$vpn_collections"

# MFA 임시 세션은 실행자가 별도로 준비합니다.
# 이 스크립트는 인증정보를 생성하거나 파일에 저장하지 않습니다.
exec "$vpn_venv/bin/python" /usr/bin/ansible-playbook "$@"
