# ���� HPA ���Ͽ� ���� ������Dump GO ���� ����

## ������ ���� ��ġ

�� ������ �غ�� �ڵ��� ���� �Է¡����ɡ��������簳�� �����Ѵ�. ���� �ۼ��̳� PR �Խð� ���� HPA/����/���� ����/ROSA ���� ���ø� ������� �ʴ´�. ������� ������. #85�� �⵿/������ #89�� ���� Replica ����/�Ѹ� ������ �������� �ʴ´�.

| ���� | ���� ��ġ | �ϴ� �� |
|---|---|---|
| tools/hpa-load/control.py, run-controller.py | �� ROSA ���� Context�� �ִ� Controller jth | �ӽ� HPA��Argo Replica ����, �������ߴܡ�UID ���� |
| tools/hpa-load/load.mjs | ���� Node�� �ִ� �ܺ� �߻���/Windows PC | 0��10��50��100��200��300��100��0 VU��35�� API ���� |
| tools/hpa-load/db_probe.py | ���� RDS TLS ���� ȣ��Ʈ | ���� MariaDB ��ȣ ���ϡ�CA�� ���� �����/�ѵ��� ��ȸ |
| tools/hpa-load/prometheus.py | �� ROSA ���� Context�� �ִ� Controller | ���� ���� Token�� �޸𸮿����� ����� ���� Prometheus query ���� ���� |
| tools/cutover-run | ���� ���� Kubernetes Context����� ������ ���� �����ϴ� ��ġ(���� cp-01 ��� ��) | �ܺ� Writer ������#156 �ݿ�/�ʿ� Sync��#79 Ȯ�Ρ�GO ��������ȯ �Ǵ� ��� |

HPA�� Node�� �⺻ https�� ǥ�� ��⸸ ����. k6/JMeter/Locust�������� ��ġ�� �ʿ� ����. Python�� ǥ�� ���̺귯���� ����. �÷��������� �ڰ��� �ٸ� host�� ���� �������� �ʴ´�. ���� ���ȭ�� SSH/���� ��η� ȣ���ϰų� �ڰ��� �ִ� host���� �����Ѵ�. ���� �� ��ΰ� ������ ���� ���� �Է����� ����� ����ƴٰ� �������� �ʴ´�.

## A. HPA ������ ���� ����/�Ѹ��� �и�

���� ����/Backend �Ѹ� ����� ���� �����ϰų� �ش� �񱳸� �̹� �������� ������ ������ �ٰŸ� ����� �� �����Ѵ�. �� ������ ���ÿ� ���� �ʴ´�. �� HPA�� ������ ����� Backend �ϳ���. FE �̹�����template���⺻ Replica�� Pod UID�� �����Ѵ�.

### ���� �ǹ̿� ����

- ������ App Source `c2dad254cc407431092691f9ea5892f8324f1520`�� Guest �߱�/����, �� ���, ��ŷ API�� ����Ѵ�.
- �ִ�300���� �̹� Run Guest ������ �߱��� �� �� VU�� `GET /api/v1/rooms`�� `GET /api/v1/rankings?offset=0&limit=20`�� ����� ȣ���Ѵ�. �� VU�� ���� ��û��1�� �ֱ��̸� ���� �����̸� ��û���� ��������. VU�� ���� ��û���� ������/WSS ������� ǥ������ �ʴ´�.
- Guest �߱�/������ ���� setup/cleanup�̴�. Member ���������� �桤��ǥ�� ������ �ʴ´�. �� ������ ����� ��ü ���� ó������WSS ���뷮���� Ȯ������ �ʴ´�. �ش� ������Room WSS ������ ���� #89 �������� �����Ѵ�.
- ���� �ܰ�� D �ʾ���120/180/300/300/300/120/180/600�ʸ� �����ߴ�.35���� �ܰ� �ð� �հ��̸� setup��HPA �⵿���ʱ� metric ��⡤cleanup/��������ü ROSA ����ð��� �������� �ʴ´�.
- VU ������ CPU60% ���ޡ�HPA Ȯ���� �������� �ʴ´�. ���� ��Ȯ���� �̰���/���������� �����. CPU �۾��� ���������� �߰��ϰų� �� �ڵ带 ���� ����� ������ �ʴ´�.

### ���� �� �غ�� ���� �Է�

`tools/hpa-load/config.TEMPLATE.NOT_ACCEPTED.json`�� ��ȣ ��ġ�� �����Ѵ�. �� API/Origin/UID������ Source hash���̹����� ���� �� ���� ä�� �ۿ��� �����Ѵ�. `capacity`�� ���� ��ü/���� �ѵ������ࡤPod/Process/Engine Pool�� ��� ������ ���� ���� �ٰŷ� B�� ä���. max10�� �ĺ��̸� �Է��� ä���ٰ� �ڵ����� ������� �ʴ´�. max+surge+terminating�� ��� ���ѡ���ü/���� ���� ���꿡 ���;� �Ѵ�. ���� �� allowance�� �𸣸�0���� ���� �ʴ´�.

`db_probe_argv`�� ���� DB host���� �Ʒ� �� ���� �б� ���� probe�� ȣ���� argv��. `generator_guard_argv`�� �߻��⿡�� Controller�� guard�� ȣ���� argv��. shell ���ڿ��� �ƴ϶� �μ� �迭�̴�. ���� ���ȭ�� ����/host trust�� ����. �ڰݰ��� �μ��� ���� �ʴ´�.

```bash
# ���� DB ���� host. ���� config�� ��ȣ ��ο� �����ϸ� �ڰ� ���� ��ü�� �̵����� �ʴ´�.
python3 -I "$B_INFRA/tools/hpa-load/db_probe.py" --config "$B_HPA_CONFIG" --execute
# �߻����� guard argv�� �θ� Controller ����.
python3 -I "$B_INFRA/tools/hpa-load/control.py" guard \
  --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" --execute
```

### Controller�� ���� ������

�Ʒ��� ���� ���� ���ø� ���� �� ����ϴ� �����̴�. `--execute` ���� �⺻ ������ ���� �Է� ������ �Ѵ�. ���� Run/work�� �����ϸ� ���� ����� �Ҹ��� �� �� work�� ��ȸ���� �ʴ´�.

```bash
HPA="$B_INFRA/tools/hpa-load"
# �⺻ �˻�: API/SQL/���� ����. ������ ���� �Է��� ��θ� ����Ѵ�.
python3 -I "$HPA/run-controller.py" plan --config "$B_HPA_CONFIG" --work "$B_HPA_WORK"
# ó�� �� ������ ������ ���� Source/UID/Ready/�̹�����DB ���簪�� Ȯ���� ��ȹ ����.
python3 -I "$HPA/run-controller.py" plan --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" --execute
# �� Run�� HPA�� �ӽ� Replica ���� ����, �ִ�45�� ������ ����. ���� ��ü�� ����0.
python3 -I "$HPA/run-controller.py" activate --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" --execute
```

HPA�� ���� min�� config �����̸� �ʱ� Pod ���� �׻�2��� ������ ���� �ʴ´�. ���� Pod ���� config�� ������ �¾ƾ� �Ѵ�. Argo �ڵ� Sync/���� �� operation���ٸ� Backend HPA�� ������ �����Ѵ�. �ӽ÷� Backend `/spec/replicas`�� ignoreDifferences��RespectIgnoreDifferences�� �����ϰ� ���� Application�� ������ spec/Source�� �����Ѵ�. �̹������Ѹ� ����/template��FE Pod ������ ���� �ߴ� ������.

`plan.json`�� ���� �߻��⿡ �����Ѵ�. ��й�ȣ��Cookie��Token�� ���Ե��� �ʴ´�. config���� ��й�ȣ�� ���� �ʴ´�. API���� ���� ��ΰ� �ִ� ������ GitHub�� �Խ����� �ʴ´�.

```powershell
# ���� Windows �߻���. ���� ���� Source�� load.mjs�� ���� Node ��� ���.
& $B_Node "$B_Infra\tools\hpa-load\load.mjs" --plan $B_HpaPlan
# ���� ���� ���� ���� �ÿ��� �Ʒ� --execute ������ ����Ѵ�. �ű� ��� ���� �ʿ�.
& $B_Node "$B_Infra\tools\hpa-load\load.mjs" --plan $B_HpaPlan `
  --out $B_HpaLoadOut --abort-file $B_HpaAbortFile --execute
```

�߻���� ó�� HPA metric �غ� �ִ�120�� ��ٸ���. ���� guard��45�� �Ѱ� ���ų� Source/UID������ DB ��ü/���� ������Pod ���°� ���� ������ �� ��û�� �����. HTTP401/403 �Ǵ� �ֱ� �ִ�100��(�ּ�20��)���� ������/�߸��� ������5%�� ������ ���ϸ� �ߴ��Ѵ�. �̴� ��ȣ�� �ߴ� �����̸� D�� ������5xx1% ���� ���ذ� �����Ѵ�. �⺻ TLS ��������û��5�� ���� timeout���ִ�1MiB ���� ������ �����Ѵ�.

### �ߴܡ��Ҹ�Ȯ ���������

```bash
# Controller���� �ߴ�: guard�� ��� STOP, ���� HPA�� ������ Disabled.
python3 -I "$HPA/run-controller.py" stop --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" --execute
# ����/patch ����� �Ҹ��� �� ���� �б� ���� ����. �ڵ� �����/���� �� ��.
python3 -I "$HPA/control.py" reconcile --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" --execute
```

�߻��⿡���� ������ abort ������ ������ �ߴ��� �� �ִ�. ����/�ߴ� ��� ���� �� ��û�� ���� �� ���� Guest ���Ǹ� ���Ḧ �õ��ϰ� `receipt.json`�� �����. ���� ������ �ִ�60�ʿ� ������ ��û timeout������ �����ϸ� ��Ȯ�� ������ �����Ѵ�. ���� Ű�� �޸𸮿��� �ְ� raw/���/�α׿� ������� �ʴ´�. ��� �Ҹ� Guest �߱��� �ڵ� ��õ����� �ʴ´�. �������� ���� Guest ������ �˷��� ����/�ΰ� ������ ����� ��� �����ƴٰ� ���� �ʴ´�.

�߻����� `receipt.json`�� Controller ��ȣ ��ġ�� ������ �� �Ʒ��� �����Ѵ�. ���� raw�� ��Ʈ��ũ ��γ� �ڰ��� GitHub�� �ø��� �ʴ´�.

```bash
python3 -I "$HPA/run-controller.py" finish --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" \
  --load-receipt "$B_HPA_LOAD_RECEIPT" --execute
# ���� Replica/observedGeneration/Ready���ӽ� HPA ���硤���� Application spec Ȯ��.
python3 -I "$HPA/control.py" verify-restore --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" --execute
```

������ API�� UID precondition���� �̹� HPA�� �����ϰ� ���� Replica��Application spec�� �����Ѵ�. �ٸ� HPA/UID/spec�� ��Ÿ���� ����� �ʴ´�. ���� ��û ������ ���� Ready ������ �и��Ѵ�. ���� �Ҹ��� ���� reconcile/verify-restore�� ���¸� ����, �ʿ��� �ܿ� �ܰ迡�� `--resume-reviewed`�� �����Ѵ�. ���μ��� PID ��ϸ� ���� ���� kill���� �ʴ´�.

���ϸ� �� ���� �������� ���� �κ� ������ ������ B�� ����� Ȯ���� ��쿡�� `control.py restore ... --confirm-no-load-started --execute`�� ����Ѵ�. ���� �߻��Ⱑ ���� �ִٸ� �� �ɼ��� ���� �ʴ´�. ���� Argo spec���� �̹� �����ƴٸ� restore�� �ٽ� �������� ���� verify-restore�� �����Ѵ�.

### ��� ������ ���� ����

�ڵ� ����: �ܰ躰 ����/����, ��û�� HTTP/JSON ��ȿ����latency���и���õ� ����, p95/p99/RPS, �߻��� CPU/�޸�/event-loop, HPA ����/��ǥ������/��� �ð�, Pod UID/Node/Ready/����ۡ�CPU/�޸�, DB ��ü/���� �����������, ���� receipt. ��Ʈ��ũ/Route�� ������ ���� label������ ��θ� D ���� query�� ������ Prometheus �����Ⱑ �߰� �����Ѵ�. �⺻ ����͸� ��ġ/smoke�� �ݺ����� �ʴ´�.

```bash
python3 -I "$HPA/results.py" --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" \
  --load-out "$B_HPA_LOAD_OUT_ON_THIS_HOST" --out "$B_NEW_HPA_SUMMARY"
```

���� Run/UID/config������ checksum�� ���� ���� receipt�� �ʼ� �����Ѵ�. ��û�� �ִ� ��� �ܰ���1~6(10/50/100/200/300/100 VU) �������� N>0 ��5xx ����<1%�� �䱸�Ѵ�. �ִ�300 VU�� ����100 VU�� ���� �����̸� ������ �ϴ� ���ܰ� �ƴϴ�. ��� �ܰ�� ������ǥ��0��NoData�����ع̴��̸� ��ü�� PARTIAL/exit2��. 0 VU��0/7�ܰ�� �� ��û���� ���ؿ��� �����Ѵ�.

����� all_nonzero_phases_http_5xx_below_1_percent��6�� �ܰ��� ��ü �����̸� phase_acceptance���� �ܰ躰 phase/vus/N/������/������ ���´�. ����1~4�ܰ踸 ����Ű�� �ʵ�� ������� �ʴ´�. ���� Ȯ��/��ҡ�ǥ��/������ ������ �����Ѵ�. ��û0/NoData�� ����0���� �ٲ��� �ʴ´�. �÷����� �߻��� UTC�� ��� ���������� �����ϸ� ���� �ٸ� ȣ��Ʈ �ð��� ���� ��Ȯ�� Ȯ�� ������ ������ �ʴ´�. CPU�� ��ǥ�� ���� �ʾҴٰ� ��¥ �����̳� �ִ� ���뷮�� ������ �ʴ´�.

## B. ���� ���� �ߴܰ� Data ���� Dump GO�� ���� �غ�

���� ����/GO�� �غ�� ���� ����/������ ������. ������ ���� #79 verifier�� �ؽ÷� ������ �����ϰ� #156�� ���� head/manifest�� Ȯ���Ѵ�. ���� �ۼ� �� #156�� open��Ready for review���̺���, reviewer ggbun2 ��û ���´�. ���� head ����/üũ�� ���� ���� �� �ٽ� �����Ѵ�. �� ��� ������ ��ٸ��� �ʰ� ���� ���並 ������ �� �ִ� �����̸� ������ ���� ���� �ð����� �����Ѵ�.

### ���� �Է°� ���� �ٰ� ��ȯ

`tools/cutover-run/config.TEMPLATE.NOT_ACCEPTED.json`�� ���� ���� API������ Context�� ������ source-read/contract ��ġ, KST�� ������ �ð�(�ð��� ���� ISO), �ܺ� ���� �ߴ�/ȸ�� ��� ��ȣ ��θ� ä���. Context �̸�/���� ��ü�� Recovery�� ���� �ؽø����� �������� �ʴ´�. access�� ���� ���� ��ü��API������ Deployment/Root/Child UID�� �о� Ȯ���Ѵ�.

`writers.TEMPLATE`�� �ܺ� ���ԡ�CI������DB/����������/�ٸ� ���� �� ������ ���� ���� ���� �Ǵ� ���� ����/�ߴ� �ٰŸ� �����Ѵ�. **B�� ���� �ڷᡤ���� ���� ������� ��ȯ�Ѵ�. A/D���� �� ����� ������ ��û���� �ʴ´�.** command ���� argv�� Ȯ�� ������ �����ϰ�, ���� ������300�� �̳��� ��� �ٰŸ� ����Ѵ�. ��Ȯ�� Writer�� �������� �������� �ʴ´�. �� ������ �𸣴� CI/DB ��θ� �����ϰų� ���� SQL/���� ������ ������ �ʴ´�.

`rotation.TEMPLATE`�� A�� ���� `rot_check 7200` �����Ȯ�� �ð������� ��� �������׳��� ���� ���� �ð��� B�� �����Ѵ�.1800�� �˻�� ���� ���� ���� �غ� �����ϰ�7200�� Ȯ���� ������� �ʴ´�. �ڵ� ȸ���� ���ų� ������ ���� ��й�ȣ�� ��ȸ���� �ʴ´�. ���� ���� ���� ���� ���� ���°� �ʿ��ϸ� ���� �Ϸ� �˻��� �ݺ��� �ٸ���.

���� ����/GO���� �� ROSA�� ���� platform handoff(state ready/�� Cluster ID)�� ä�� ���� ���䡤�� Worker/Data ���� �غ�A ���� �غ� �ٰŵ� �����Ѵ�. �ش� ������ ����/���� �帧���� ��´�. ���� ������ ������ ������ �ʴ´�. ���� Import�� �� ���� ������ Dump �� �������� �䱸�ϴ� ��ȯ ������ ������ �ʴ´�.

### ���� ���� ����

�Ʒ� `CUT` ������ ���� kubeconfig/Context�� �ִ� host���� �����Ѵ�. �ڵ� Git ���� ��δ� ���� `github_api_argv`(��: ��ȣ ������ �غ�� gh api �Ǵ� ���� Controller ȣ�� argv)�� ����Ѵ�. ������ B�� ���� �ð��� ���� GitHub ȭ�鿡�� �����ϰ� `stop --adopt-existing-merge`�� ���� PR/head������ â ���� �ֱ� ������ �����Ѵ�. ���Ŀ� �ܺ� ����/ȸ���� ������ ��ó�� �ұ� ����� �� ����.

```bash
CUT="$B_INFRA/tools/cutover-run"
# �⺻ �Է� ����: ���� ����/GO ����.
python3 -I "$CUT/cutover.py" prepare --config "$B_CUTOVER_CONFIG" --work "$B_CUTOVER_WORK" --infra "$B_INFRA"
# ���� ����/������ ����. API ��ȸ/����/GO ����.
python3 -I "$CUT/cutover.py" prepare --config "$B_CUTOVER_CONFIG" --work "$B_CUTOVER_WORK" --infra "$B_INFRA" --execute
# ���� ���� ���� ������ ���� �� �Ʒ� �б�� Ȯ��. �� �α��� ���� �ݺ� �ƴ�.
python3 -I "$CUT/cutover.py" access --config "$B_CUTOVER_CONFIG" --work "$B_CUTOVER_WORK" --infra "$B_INFRA" --execute
# ������ ���� ���� �ð�����: ���� �ܺ� Writer ����/Ȯ�� �Ǵ� ���� �ٰŸ� ����.
python3 -I "$CUT/writers.py" --plan "$B_WRITER_PLAN" --work "$B_WRITER_WORK" --execute
```

`external-hold.current.json`�� cutover config�� external_hold ��η� ����Ѵ�. ���� ������ �����Ѵ�. Writer ���� ����� �Ҹ��̸� `writers.py ... --verify-only --execute`�� ���� Ȯ�θ� �����Ѵ�. �̹� �õ��� ������ �ڵ� ���������� �ʴ´�.

```bash
# ���� �ܺ� ������Aȸ��������â������ ���١�#156 ����/üũ�� Ȯ���� �ڿ��� ����.
python3 -I "$CUT/cutover.py" stop --config "$B_CUTOVER_CONFIG" --work "$B_CUTOVER_WORK" --infra "$B_INFRA" --execute
# ���� �ڵ� Sync�� ���� ���̸� ������. �ʿ��� �� child�� ���� merge�� Replica0 Sync 1ȸ.
python3 -I "$CUT/cutover.py" sync --config "$B_CUTOVER_CONFIG" --work "$B_CUTOVER_WORK" --infra "$B_INFRA" --execute
# �̹� ���� ������ #79�� ���� �ڵ�� Ȯ��. �Ϸ� receipt�� ������ �ٽ� �˻����� ����.
python3 -I "$CUT/cutover.py" verify --config "$B_CUTOVER_CONFIG" --work "$B_CUTOVER_WORK" --infra "$B_INFRA" --execute
# ���ݵ� ���� �ִ������ܺ�����/ȸ���� ��ȿ���� �����ϰ� GO�� 1ȸ ����.
python3 -I "$CUT/cutover.py" go --config "$B_CUTOVER_CONFIG" --work "$B_CUTOVER_WORK" --infra "$B_INFRA" --execute
```

GitHub ȭ�鿡�� �̹� ������ ��� stop ���ɿ��� `--adopt-existing-merge`�� �߰��Ѵ�. ���� head������ â��5�� �̳� ���� ������ ���� API�� �����ϰ� ������ ��õ����� �ʴ´�. PR156�� ���� ���̰ų� head�� �޶������� ������ ������. Source Sync ��� �Ҹ��� sync ǥ��/���� operation�� �����ϸ� �ߺ� Sync�� �������� �ʴ´�.

GO ���� ������ `READY_TO_RELAY_DATA_FINAL_DUMP_GO`, ��� ������ GO.json�̴�. **B�� �� message�� A���� ���� ������ �� A�� ���� #44�� ���� Ȯ�Ρ����� Dump/Import������/Grant��Schema/�񱳸� �����Ѵ�.** ������ �޽����� �ڵ� �߼����� �ʴ´�. GO���� ���� Run/merge/�ٰ� �ؽá���ȿ ���ᡤ���� ���� ���� ������ ����.75�� ����/120�� ���� �ð��� �ʱ� �������� �Ϸ�ð� ������ �ƴϴ�.

���� ������ �˻� ���� ���� STOP/HOLD�� �����ϰ� ������ ���� �簳���� �ʴ´�. ���� ���� �� �� Cloud ���� ���Ⱑ �������� �ܼ� ���� �簳�� �����Ѵ�. A ������ ������ B ����SQL/current������ �ٰŸ� ������ ���� ��ȯ/���� ������ ����Ѵ�.

```bash
python3 -I "$CUT/cutover.py" decision --choice HOLD \
  --data-decision "$B_DATA_DECISION" --config "$B_CUTOVER_CONFIG" --work "$B_CUTOVER_WORK" --infra "$B_INFRA" --execute
# CLOUD/SOURCE ������ ���� ���ǡ����� ���� �� ����Ѵ�. �ڵ� Source �簳/DB rollback ����.
```

## ���� ������ ���� ���� ��

1. ����: ����� ���� ����, ���� ���/���������� ���� �ð�, �� �÷���/���� Context��UID/Source������ ���� ���.
2. HPA: ���� ���/Pod��Process/Pool��DB ��ü/���� �ѵ�/���� �ٰ�, ���� �߻��� Node ��Ρ�Controller guard ȣ�⡤DB probe host/��ȣ ���� ���, D ���� metric/query������ ���. min/max�� �� �ٰŷ� ä���Ѵ�. ��ũ��Ʈ/���� ���� ��ġ ������ D���� �ٽ� �䱸���� �ʴ´�.
3. ����/GO: ���� ���� Context/API����� ����, �ܺ� Writer ���/���� ���� ���� �Ǵ� ���� ����/�ߴ� �ٰ�, A ���� ȸ�� Ȯ�Ρ��̰� ���� �ð�. ���� Data ����� GO �� ���� ���࿡�� �����.

�� RDS/Valkey/NAT/EIP/��� ��� �ڿ��� �� ������ ������ �ʴ´�. HPA�� ���� ����� Pod�� �����Ѵ�. �ʿ� �ڿ��� �����ϸ� �ڵ� ��� Ȯ��/���� ���� ��� �ߴ�/�κ� �������� �����. ���� ��ġ/Pull/TLS/UI/smoke/����Ѹ���Snapshot/�ص� �˻�� �̹� �غ񿡼� �ݺ����� �ʾҴ�. �� �غ� �ڵ��� �ռ� ������ ���� ���� ������ ������.

���� MachineAutoscaler�� min/max�� �ٸ� ���� ��� Ȯ���� ������ ������ ��ȹ/������ �����Ѵ�. HPA ������ ���� ��� Ȯ��Ⱑ �߰� ���� ��带 ������ �ʵ��� �ϱ� ���� �б� ���� Ȯ���̸� �� ������ MachinePool ������ �ڵ� �������� �ʴ´�. �ش� ��ȸ ����/���� ���� ���δ� �� ȯ�濡�� Ȯ���Ѵ�.
