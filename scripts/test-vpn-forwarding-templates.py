#!/usr/bin/env python3
# 작성자: 이유빈
# 작성 내용: VPN 전달·방화벽 설정 틀의 오프라인 검사

import base64
import importlib.util
import ipaddress
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

sys.dont_write_bytecode = True
root = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "vpn_public_checker",
    root / "scripts/prepare-vpn-public-configs.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

sample = yaml.safe_load(
    (root / "ansible/vars/vpn_wireguard.example.yml").read_text(
        encoding="utf-8"
    )
)
sample.update({
    "vpn_wireguard_onprem_public_key":
        base64.b64encode(bytes(range(32))).decode(),
    "vpn_wireguard_aws_public_key":
        base64.b64encode(bytes(range(1, 33))).decode(),
    "vpn_wireguard_aws_public_endpoint": "8.8.8.8",
    "vpn_wireguard_onprem_job_host_cidrs": ["192.168.52.50/32"],
    "vpn_wireguard_aws_destination_cidrs": [
        "192.168.70.0/24",
        "192.168.71.0/24",
        "192.168.72.0/24",
    ],
    "vpn_wireguard_allowed_tcp_ports": [3306],
})

values = module.validate(
    sample,
    {"onprem_job_host_cidrs": ["192.168.52.50/32"]},
)

environment = Environment(
    loader=FileSystemLoader(str(root / "ansible/templates")),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


# 키 주입 시점이 되돌아가거나 개인키가 본문에 추가되면 중단합니다.
def valid_key_hook(text):
    entries = [
        (line.split("=", 1)[0].strip().casefold(), line.split("=", 1)[1].strip())
        for line in text.splitlines()
        if "=" in line and not line.lstrip().startswith(("#", ";"))
    ]
    hooks = [value for key, value in entries if key == "postup"]
    return (
        hooks == ["/usr/bin/wg set %i private-key /etc/wireguard/keys/%i.key"]
        and not any(key in ("preup", "privatekey") for key, value in entries)
    )


for template_name in ("vpn_aws_wg0.conf.j2", "vpn_onprem_wg0.conf.j2"):
    rendered = environment.get_template(template_name).render(values)
    if not valid_key_hook(rendered):
        raise SystemExit("중단: 키 주입은 지정 PostUp 한 개만 허용하며 PreUp·PrivateKey는 없어야 합니다.")

    bad_samples = [
        rendered.replace("PostUp =", "PreUp =", 1),
        rendered + "\nPrivateKey = 검사전용가짜값\n",
        rendered + "\nPostUp = 중복검사명령\n",
    ]
    if any(valid_key_hook(text) for text in bad_samples):
        raise SystemExit("중단: 잘못된 키 설정을 재발 방지 검사가 놓쳤습니다.")

print("통과: 양쪽 설정의 PostUp 명령·PreUp 없음·개인키 본문 없음")
print("통과: PreUp 되돌림·개인키 본문 추가·PostUp 중복의 차단 사례 6개")

if sys.argv[1:] == ["--key-hook-only"]:
    print("검사 범위: 키 주입 관련 검사만 실행. 기존 전달·원복 시험과 실제 서버 실행 없음")
    raise SystemExit(0)

if sys.argv[1:]:
    raise SystemExit("중단: 지원하지 않는 검사 실행 인자입니다.")

names = [
    "vpn_onprem_zone.xml.j2",
    "vpn_onprem_to_tunnel.xml.j2",
    "vpn_onprem_from_tunnel.xml.j2",
]
documents = [
    ET.fromstring(environment.get_template(name).render(values))
    for name in names
]
zone, outbound, inbound = documents

if (
    zone.tag != "zone"
    or zone.get("target") != "DROP"
    or len(zone.findall("interface")) != 1
    or zone.find("interface").get("name") != "wg0"
    or zone.find("source") is not None
    or zone.find("forward") is not None
):
    raise SystemExit("중단: VPN 전용 구역의 범위가 예상과 다릅니다.")

if (
    outbound.tag != "policy"
    or inbound.tag != "policy"
    or outbound.get("target") != "DROP"
    or inbound.get("target") != "DROP"
    or outbound.get("priority") != "-100"
    or inbound.get("priority") != "-100"
):
    raise SystemExit("중단: 전달 정책의 기본 차단 설정이 다릅니다.")

expected_directions = [
    (outbound, "ANY", "seokpan-vpn"),
    (inbound, "seokpan-vpn", "ANY"),
]
for document, ingress, egress in expected_directions:
    if (
        len(document.findall("ingress-zone")) != 1
        or len(document.findall("egress-zone")) != 1
        or document.find("ingress-zone").get("name") != ingress
        or document.find("egress-zone").get("name") != egress
    ):
        raise SystemExit("중단: 전달 정책의 통신 방향이 다릅니다.")

if inbound.findall("rule"):
    raise SystemExit("중단: 예상하지 않은 역방향 허용 규칙이 있습니다.")

for document in documents:
    if (
        document.find(".//masquerade") is not None
        or document.find(".//forward-port") is not None
        or document.find("port") is not None
        or document.find("service") is not None
    ):
        raise SystemExit("중단: 예상하지 않은 전체 허용 또는 주소 변환 설정이 있습니다.")

rules = []
for rule in outbound.findall("rule"):
    if (
        rule.get("family") != "ipv4"
        or sorted(child.tag for child in rule)
        != ["accept", "destination", "port", "source"]
    ):
        raise SystemExit("중단: 전달 규칙의 구조가 다릅니다.")

    rules.append((
        rule.find("source").get("address"),
        rule.find("destination").get("address"),
        rule.find("port").get("protocol"),
        rule.find("port").get("port"),
    ))

expected_rules = {
    (host, destination, "tcp", str(port))
    for host in values["vpn_wireguard_onprem_job_host_cidrs"]
    for destination in values["vpn_wireguard_aws_destination_cidrs"]
    for port in values["vpn_wireguard_allowed_tcp_ports"]
}

if set(rules) != expected_rules or len(rules) != len(expected_rules):
    raise SystemExit("중단: 승인한 서버·목적지·포트의 규칙과 다릅니다.")


def permits(source, destination, protocol, port):
    return any(
        ipaddress.IPv4Address(source) in ipaddress.IPv4Network(host)
        and ipaddress.IPv4Address(destination) in ipaddress.IPv4Network(network)
        and protocol == expected_protocol
        and str(port) == expected_port
        for host, network, expected_protocol, expected_port in rules
    )


cases = [
    (True, "192.168.52.50", "192.168.70.10", "tcp", 3306),
    (True, "192.168.52.50", "192.168.71.10", "tcp", 3306),
    (True, "192.168.52.50", "192.168.72.10", "tcp", 3306),
    (False, "192.168.52.51", "192.168.70.10", "tcp", 3306),
    (False, "192.168.52.50", "192.168.69.10", "tcp", 3306),
    (False, "192.168.52.50", "192.168.70.10", "tcp", 6379),
    (False, "192.168.52.50", "192.168.70.10", "udp", 3306),
    (False, "192.168.52.50", "192.168.70.10", "tcp", 22),
]

if any(
    permits(source, destination, protocol, port) != expected
    for expected, source, destination, protocol, port in cases
):
    raise SystemExit("중단: 전달 규칙의 허용·차단 사례가 다릅니다.")

forward = environment.get_template(
    "vpn_ip_forward.conf.j2"
).render(values)
settings = [
    line.strip()
    for line in forward.splitlines()
    if line.strip() and not line.lstrip().startswith("#")
]
if settings != ["net.ipv4.ip_forward = 1"]:
    raise SystemExit("중단: 통신 전달 설정 범위가 다릅니다.")

print("통과: XML 구조·VPN 전용 구역·양방향 정책 범위")
print("통과: 승인 서버·Data 목적지·TCP 3306 규칙 일치")
print("통과: 허용·차단 사례 8개")
print("통과: IPv4 전달 설정 후보")
print("확인: 실제 firewalld 해석·기존 규칙과의 결합·응답 통신 검사는 별도입니다.")
print("미실행: 서버 접속·방화벽 변경·통신 전달 활성화·VPN 실행")
