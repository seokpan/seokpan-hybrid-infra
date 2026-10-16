# 작성자: 이유빈
# 작성 내용: 실제 VPN 작업 순서를 이용한 격리 원복 시험
import copy
import hashlib
import json
import os
import pwd
import subprocess
import sys
import tempfile
from pathlib import Path
import yaml

STUB = r'''import json, sys, xml.etree.ElementTree as ET
from pathlib import Path
state_file = Path(sys.argv[1])
kind = sys.argv[2]
args = sys.argv[3:]
state = json.loads(state_file.read_text())
server = state_file.parent / "server"
stage = ""
if kind == "firewall":
    if args == ["--check-config"]:
        for path in (server / "etc/firewalld").rglob("*.xml"):
            ET.parse(path)
        stage = "check"
    elif args == ["--reload"]:
        stage = "reload"
    else:
        raise SystemExit(90)
elif kind == "forward":
    if len(args) != 2 or args[0] != "-w" or args[1] not in ["net.ipv4.ip_forward=0", "net.ipv4.ip_forward=1"]:
        raise SystemExit(91)
    state["forward"] = args[1][-1]
    stage = "forward"
elif kind == "service":
    if len(args) != 2:
        raise SystemExit(92)
    stage = "start" if args[1] == "started" else "stop"
else:
    raise SystemExit(93)
state["calls"][stage] = state["calls"].get(stage, 0) + 1
failed = state["faults"].get(stage) == state["calls"][stage]
if kind == "service" and not (stage == "stop" and failed):
    state["enabled"] = args[0].lower() == "true"
    state["active"] = stage == "start"
if kind == "firewall" and stage == "reload" and not failed:
    state["managed_firewall"] = sorted(
        path.name for path in (server / "etc/firewalld").rglob("seokpan*.xml")
    )
state["events"].append(stage + (":failed" if failed else ":ok"))
state_file.write_text(json.dumps(state))
print("모의 명령 실패" if failed else "모의 명령 성공")
raise SystemExit(21 if failed else 0)
'''


def stop(message):
    raise SystemExit("중단: " + message)


def main():
    if pwd.getpwuid(os.getuid()).pw_name != "ansible":
        stop("본인의 ansible 계정에서 실행하세요.")
    root = Path(__file__).resolve().parents[1]
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=root, capture_output=True, text=True)
    if branch.returncode or branch.stdout.strip() != "infra/16-vpn-resume-20261013":
        stop("VPN 작업 브랜치가 아닙니다.")
    source = root / "ansible/playbooks/vpn_onprem_configure.yml"
    expected = Path.home() / ".local/share/seokpan-hybrid/vpn-execution/onprem-config-safety-check-cx0ekrnv/safety-check-result.json"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if json.loads(expected.read_text(encoding="utf-8")).get("파일 확인값") != digest:
        stop("이전 검사 후 작업 코드가 변경됐습니다.")
    play = yaml.safe_load(source.read_text(encoding="utf-8"))[0]
    if len(play["tasks"]) != 1 or not {"block", "rescue"} <= set(play["tasks"][0]):
        stop("적용과 원복 작업 구조가 다릅니다.")
    if set(play["vars"]["vpn_new_files"]) != {
        "/etc/firewalld/zones/seokpan-vpn.xml",
        "/etc/firewalld/policies/seokpan_to_vpn.xml",
        "/etc/firewalld/policies/seokpan_from_vpn.xml",
        "/etc/sysctl.d/90-seokpan-vpn.conf",
        "/etc/wireguard/wg0.conf",
    }:
        stop("원복 파일 범위가 예상과 다릅니다.")
    base = Path.home() / ".local/share/seokpan-hybrid/vpn-execution"
    record = Path(tempfile.mkdtemp(prefix="onprem-rollback-mock-", dir=base))
    print("보호 시험 기록 위치:", record, flush=True)
    helper = record / "mock-command.py"
    helper.write_text(STUB, encoding="utf-8")
    os.chmod(helper, 0o600)
    config = record / "ansible.cfg"
    config.write_text("[defaults]\nstdout_callback = default\nretry_files_enabled = False\n", encoding="utf-8")
    inventory = record / "inventory.yml"
    inventory.write_text(yaml.safe_dump({"all": {"hosts": {"rollback_probe": {"ansible_connection": "local", "ansible_python_interpreter": sys.executable}}}}), encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("AWS_", "TF_", "ANSIBLE_"))}
    environment.update({"ANSIBLE_CONFIG": str(config), "ANSIBLE_NOCOLOR": "true", "PYTHONNOUSERSITE": "1"})
    cases = [
        ("정상 적용", {}, False, True),
        ("방화벽 해석 실패", {"check": 1}, False, False),
        ("방화벽 다시 읽기 실패", {"reload": 1}, False, False),
        ("전달 설정 중 실패", {"forward": 1}, False, False),
        ("서비스 시작 중 부분 실패", {"start": 1}, False, False),
        ("원래 자동 시작이 켜진 상태의 실패", {"start": 1}, True, False),
        ("원복 중 서비스 중지 실패", {"start": 1, "stop": 1}, False, False),
    ]
    results = []
    for number, (label, faults, enabled, success) in enumerate(cases, 1):
        print("시험 중:", str(number) + "/" + str(len(cases)), label, flush=True)
        case = record / ("case-" + str(number))
        case.mkdir(mode=0o700)
        server = case / "server"
        def rewrite(value):
            if isinstance(value, str):
                if value.startswith("../templates/"):
                    return str(root / "ansible/templates" / Path(value).name)
                return value.replace("/etc/", str(server / "etc") + "/")
            if isinstance(value, list):
                return [rewrite(item) for item in value]
            if isinstance(value, dict):
                return {key: rewrite(item) for key, item in value.items()}
            return value
        variables = rewrite(copy.deepcopy(play["vars"]))
        directories = variables["vpn_directory_paths"]
        for item in directories:
            Path(item["path"]).mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(item["path"], 0o700)
        modes = {item["path"]: Path(item["path"]).stat().st_mode & 0o777 for item in directories}
        baseline = server / "etc/firewalld/zones/public.xml"
        baseline.write_text('<zone><short>기존 방화벽 시험 사본</short></zone>\n', encoding="utf-8")
        baseline_hash = hashlib.sha256(baseline.read_bytes()).hexdigest()
        generated = case / "candidate/generated"
        generated.mkdir(parents=True)
        (generated / "onprem-wg0.conf").write_text("# 시험용 공개 설정\n[Interface]\n", encoding="utf-8")
        state_file = case / "state.json"
        state_file.write_text(json.dumps({"forward": "0", "enabled": enabled, "active": False, "managed_firewall": [], "faults": faults, "calls": {}, "events": []}), encoding="utf-8")
        variables.update({
            "vpn_allow_onprem_service_start": True,
            "vpn_candidate": {"path": str(case / "candidate")},
            "vpn_original_forward": {"stdout": "0"},
            "vpn_original_enabled": {"stdout": "enabled" if enabled else "disabled"},
            "vpn_existing_directories": {"results": [{"item": item, "stat": {"exists": True}} for item in directories]},
            "vpn_settings": {"vpn_wireguard_interface": "wg0", "vpn_wireguard_onprem_job_host_cidrs": ["192.168.52.50/32"], "vpn_wireguard_aws_destination_cidrs": ["192.168.70.0/24", "192.168.71.0/24", "192.168.72.0/24"], "vpn_wireguard_allowed_tcp_ports": [3306]},
        })
        operation = rewrite(copy.deepcopy(play["tasks"][0]))
        allowed = {"ansible.builtin.file", "ansible.builtin.template", "ansible.builtin.copy", "ansible.builtin.command", "ansible.builtin.systemd", "ansible.builtin.debug", "ansible.builtin.fail"}
        for section in ("block", "rescue"):
            for task in operation[section]:
                modules = [key for key in task if key.startswith("ansible.")]
                if len(modules) != 1 or modules[0] not in allowed:
                    stop("시험하지 못하는 작업 모듈이 있습니다.")
                module = modules[0]
                payload = task[module]
                if module in {"ansible.builtin.file", "ansible.builtin.template", "ansible.builtin.copy"}:
                    payload.pop("owner", None)
                    payload.pop("group", None)
                elif module == "ansible.builtin.command":
                    argv = payload.get("argv", [])
                    if not argv or argv[0] not in ("firewall-cmd", "sysctl"):
                        stop("실제 명령 실행을 차단했습니다.")
                    kind = "firewall" if argv[0] == "firewall-cmd" else "forward"
                    payload["argv"] = [sys.executable, str(helper), str(state_file), kind] + argv[1:]
                elif module == "ansible.builtin.systemd":
                    if payload.get("name") != "wg-quick@wg0":
                        stop("서비스 시험 범위가 다릅니다.")
                    task.pop(module)
                    task["ansible.builtin.command"] = {"argv": [sys.executable, str(helper), str(state_file), "service", str(payload["enabled"]), payload["state"]]}
        shadow = [{"name": "격리 원복 시험", "hosts": "all", "gather_facts": False, "become": False, "vars": variables, "tasks": [operation]}]
        test_file = case / "test.yml"
        test_file.write_text(yaml.safe_dump(shadow, allow_unicode=True, sort_keys=False), encoding="utf-8")
        log = case / "ansible.log"
        with log.open("w", encoding="utf-8") as stream:
            executed = subprocess.run([sys.executable, "/usr/bin/ansible-playbook", "-i", str(inventory), str(test_file)], env=environment, stdout=stream, stderr=subprocess.STDOUT, text=True)
        state = json.loads(state_file.read_text(encoding="utf-8"))
        new_files = [Path(path).exists() for path in variables["vpn_new_files"]]
        if hashlib.sha256(baseline.read_bytes()).hexdigest() != baseline_hash:
            stop("기존 방화벽 시험 사본이 변경됐습니다.")
        if any(Path(path).stat().st_mode & 0o777 != mode for path, mode in modes.items()):
            stop("기존 폴더 권한 유지 시험에 실패했습니다.")
        if success:
            valid = executed.returncode == 0 and all(new_files) and state["forward"] == "1" and state["active"] and state["enabled"]
        else:
            valid = executed.returncode != 0 and not any(new_files) and state["forward"] == "0" and not state["managed_firewall"]
            if "stop" in faults:
                valid = valid and state["active"] and "stop:failed" in state["events"] and "reload:ok" in state["events"] and "forward:ok" in state["events"]
            else:
                valid = valid and not state["active"] and state["enabled"] == enabled
            valid = valid and "원복을 시도했습니다" in log.read_text(encoding="utf-8", errors="replace")
        if not valid:
            stop("실패 지점별 결과가 예상과 다릅니다. 보호 기록: " + str(case))
        results.append({"사례": label, "결과": "예상과 일치", "원복 중 실패 주입": "stop" in faults})
        print("통과:", label, flush=True)
    if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        stop("시험 중 원본 작업 파일이 변경됐습니다.")
    summary = record / "result.json"
    summary.write_text(json.dumps({"시험 수": len(results), "결과": results, "원본 작업 파일 확인값": digest, "실제 서버 원복 시험": False}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("완료: 실제 작업 순서를 사용한 격리 시험", len(results), "개")
    print("확인: 원복 중 실패 사례는 나머지 원복을 계속하고 실패를 알리는 동작을 검사했습니다.")
    print("확인: 방화벽과 서비스 명령은 모의 명령이며 실제 서버 원복 성공 판정은 아닙니다.")
    print("미실행: 서버 접속·키 읽기·방화벽 변경·서비스 시작·AWS 접속")


if __name__ == "__main__":
    main()
