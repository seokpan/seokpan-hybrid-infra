#!/usr/bin/env python3
# 작성자: 이유빈
# 작성 내용: VPN 공개 입력 검사와 양쪽 설정 후보 생성

import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined


def validate(values, foundation):
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    require(
        isinstance(values, dict) and isinstance(foundation, dict),
        "입력 형식이 다릅니다.",
    )

    allowed = {
        "vpn_wireguard_interface",
        "vpn_wireguard_aws_tunnel_address",
        "vpn_wireguard_onprem_tunnel_address",
        "vpn_wireguard_listen_port",
        "vpn_wireguard_mtu",
        "vpn_wireguard_onprem_keepalive_seconds",
        "vpn_wireguard_onprem_public_key",
        "vpn_wireguard_aws_public_key",
        "vpn_wireguard_aws_public_endpoint",
        "vpn_wireguard_onprem_job_host_cidrs",
        "vpn_wireguard_aws_destination_cidrs",
        "vpn_wireguard_allowed_tcp_ports",
    }

    require(
        set(values) == allowed,
        "공개 입력 항목이 누락되거나 예상과 다릅니다.",
    )
    require(
        values["vpn_wireguard_interface"] == "wg0",
        "현재 지원하는 인터페이스는 wg0입니다.",
    )
    require(
        values["vpn_wireguard_aws_tunnel_address"] == "10.200.0.1/30",
        "AWS 터널 주소가 설계와 다릅니다.",
    )
    require(
        values["vpn_wireguard_onprem_tunnel_address"] == "10.200.0.2/32",
        "온프레미스 터널 주소가 설계와 다릅니다.",
    )
    require(
        type(values["vpn_wireguard_listen_port"]) is int
        and values["vpn_wireguard_listen_port"] == 51820,
        "통신 포트가 설계와 다릅니다.",
    )

    mtu = values["vpn_wireguard_mtu"]
    require(
        type(mtu) is int and 1280 <= mtu <= 1500,
        "MTU 입력 범위를 확인해야 합니다.",
    )

    keepalive = values["vpn_wireguard_onprem_keepalive_seconds"]
    require(
        type(keepalive) is int and 5 <= keepalive <= 120,
        "연결 유지 간격을 확인해야 합니다.",
    )

    for name in (
        "vpn_wireguard_onprem_public_key",
        "vpn_wireguard_aws_public_key",
    ):
        key = values[name]
        require(isinstance(key, str), "공개키 형식이 다릅니다.")
        try:
            decoded = base64.b64decode(key, validate=True)
        except Exception:
            raise ValueError("공개키가 올바른 형식이 아닙니다.")

        require(
            len(decoded) == 32 and any(decoded),
            "공개키 길이 또는 값이 올바르지 않습니다.",
        )

    require(
        values["vpn_wireguard_onprem_public_key"]
        != values["vpn_wireguard_aws_public_key"],
        "양쪽 공개키가 같습니다.",
    )

    endpoint = values["vpn_wireguard_aws_public_endpoint"]
    require(isinstance(endpoint, str), "AWS 공인 주소 형식이 다릅니다.")
    address = ipaddress.IPv4Address(endpoint)
    require(
        address.is_global and not address.is_multicast,
        "AWS 연결 대상은 실제 공인 IPv4 주소여야 합니다.",
    )

    hosts = values["vpn_wireguard_onprem_job_host_cidrs"]
    expected_hosts = foundation.get("onprem_job_host_cidrs")

    require(
        isinstance(hosts, list) and hosts,
        "온프레미스 작업 서버 입력이 없습니다.",
    )
    require(
        all(isinstance(item, str) for item in hosts),
        "작업 서버 주소 형식이 다릅니다.",
    )
    require(
        isinstance(expected_hosts, list)
        and set(hosts) == set(expected_hosts),
        "작업 서버 목록이 Foundation 입력과 다릅니다.",
    )
    require(
        len(set(hosts)) == len(hosts),
        "작업 서버 주소가 중복됐습니다.",
    )

    for item in hosts:
        network = ipaddress.IPv4Network(item, strict=True)
        require(
            network.prefixlen == 32
            and not network.overlaps(
                ipaddress.IPv4Network("192.168.64.0/20")
            )
            and not network.overlaps(
                ipaddress.IPv4Network("10.200.0.0/30")
            ),
            "작업 서버 범위가 넓거나 VPN 설계와 겹칩니다.",
        )

    destinations = values["vpn_wireguard_aws_destination_cidrs"]
    require(
        isinstance(destinations, list)
        and all(isinstance(item, str) for item in destinations),
        "AWS 목적지 목록 형식이 다릅니다.",
    )
    require(
        len(destinations) == 3
        and set(destinations) == {
            "192.168.70.0/24",
            "192.168.71.0/24",
            "192.168.72.0/24",
        },
        "AWS 목적지가 현재 Data 서브넷 3개와 다릅니다.",
    )

    require(
        values["vpn_wireguard_allowed_tcp_ports"] == [3306]
        and type(values["vpn_wireguard_allowed_tcp_ports"][0]) is int,
        "현재 준비 범위는 RDS 포트 3306입니다.",
    )

    result = dict(values)
    result["vpn_wireguard_onprem_allowed_ips"] = [
        "10.200.0.2/32"
    ] + sorted(hosts)
    result["vpn_wireguard_aws_allowed_ips"] = [
        "10.200.0.1/32"
    ] + sorted(destinations)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="서버에 접속하지 않는 VPN 공개 설정 후보 생성"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--foundation-input", required=True)
    parser.add_argument("--output-dir", required=True)
    arguments = parser.parse_args()

    try:
        values = yaml.safe_load(
            Path(arguments.input).read_text(encoding="utf-8")
        )
        foundation = json.loads(
            Path(arguments.foundation_input).read_text(encoding="utf-8")
        )
        checked = validate(values, foundation)

        repo = Path(__file__).resolve().parents[1]
        templates = repo / "ansible/templates"
        environment = Environment(
            loader=FileSystemLoader(str(templates)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )

        rendered = {
            "aws-wg0.conf": environment.get_template(
                "vpn_aws_wg0.conf.j2"
            ).render(checked),
            "onprem-wg0.conf": environment.get_template(
                "vpn_onprem_wg0.conf.j2"
            ).render(checked),
        }

        output = Path(arguments.output_dir)
        if (
            not output.is_absolute()
            or output.exists()
            or output.is_symlink()
            or output.resolve().is_relative_to(repo.resolve())
        ):
            raise ValueError(
                "출력 위치는 Git 작업 폴더 밖의 아직 없는 절대 경로여야 합니다."
            )

        output.mkdir(mode=0o700)

        for name, content in rendered.items():
            with (output / name).open("x", encoding="utf-8") as stream:
                os.chmod(output / name, 0o600)
                stream.write(content)

        print("완료: 양쪽 VPN 공개 설정 후보 생성")
        print("확인: 개인키 원문을 읽거나 설정 파일에 넣지 않았습니다.")
        print("확인: 방화벽·키 공급·서버 설정 적용은 별도 준비가 필요합니다.")
        print("미실행: 서버 접속·VPN 실행·AWS 변경")

    except Exception:
        raise SystemExit(
            "중단: 입력 또는 설정 후보 생성에 실패했습니다. "
            "실제 값은 출력하지 않습니다."
        )


if __name__ == "__main__":
    main()
