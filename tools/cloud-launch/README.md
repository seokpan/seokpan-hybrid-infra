# Cloud �������⵿������ ����

���� ROSA ����/���� ���� ������ ����� �� ���� �����̴�. �غ��� �ĺ��� **���� ���� Source�� Argo inline Kustomize ����� ���� ��ü ����**�� �����, ���� Data/���� SQL/���� Schema ����� ���� �� �� ���� Application�� �ݿ��Ѵ�. ROSA ������DB ���硤Secret ���ޡ����� Deployment apply�� ���� �ʴ´�.

## �غ� �Ϸ�� ���� ���� ����

- ���� GitOps `c75a4b5e3b3b9bae4ea7d8a431962cb9ca7308d7`�� 13�� blob�� Ȯ���Ѵ�. App/���� �̹����� ���� SQL������ ������ �ٲ��� �ʴ´�.
- Backend/Frontend 1/1 �� 2/1 �� 3/3�� ���� Argo Source��8/8/10�� ��ü ���𡤹��� ���� ����� �Բ� �����. target�� ���� PDB2���л� ������ �����Ѵ�. SA�� `seokpan-cloud-runtime`�̸� ���� �ӽ� Pull ������ ���� �ʴ´�.
- �� �ּҴ� `https://seokpan.<�� ingress_domain>`���� ���Ѵ�. FE/API/WS Route�� Allowed Origins�� ���� Host�� ����ϹǷ� A�� Recovery Host�� ��ٸ��� �ʴ´�.
- ������ Release�� COMPLETE�� **�ĺ� �Է�/���� �ϰ�**�̴�. ����/���� ����� NOT RUN���� �����. Source ������ ��Ÿ�� PASS�� �ٲ��� �ʴ´�.
- Pool2/overflow0/���5�ʡ�WS5���� �̹� �ʱ� ���� �ĺ���. ���� ���࿡�� ������ �����ؾ� �ϸ� ���ɡ�Pool ���� ������ ���� ���� �����.
- ����/����/��ǥ �ܰ�� �ڵ����� ���� �⵿���� �ʴ´�. ���� ������ ���� ���� ������ ���� �ܰ��� �ٰŷ� �����Ѵ�. ������ ���� ������ ������ ���� �ܰ迡�� ���� �����ϸ� ���� ���� ������ ���ʿ��ϰ� ��ٸ��� �ʴ´�.

## ���� ��ġ�� �ʿ��� ����

Controller `jth` �͹̳ο��� ���� Infra checkout�� ����Ѵ�. Python3��PyYAML6.0.2��Kustomize5.7.1�� ���� ���� `tjung03` kubeconfig�� �ʿ��ϴ�. Python�� �ʿ��� ���̺귯���� ���ٸ� Controller �غ� �ܰ迡�� ���� ����ȯ�濡 `requirements.txt`�� ��ġ�Ѵ�. ROSA ���� �� ���� ��ġ�� ��ٸ��� �ʴ´�. `--execute`�� ���� stage/readback�� ���� package �˻縸 �ϸ� ��Ʈ��ũ0�̴�.

���� ����/������ `scripts/rosa-recreate-resume.sh`���� ������ �����Ѵ�. **�� ������ �Ʒ� ������ ����� ���ð� �ƴϴ�.** ���� ����� ������ �����Ѵ�. ���� Git���� �������̼��� �Է� Ʋ�� �����ϸ� ���� �Է�/����� ���� ��ȣ ���͸��� �д�.

## 1. ���� �� �� �ִ� ���� ��� ��ȸ

�� ROSA ���� ������ �� ������ ���� ���� ����/���Ḹ �д´�. ������ص���AWS���α��� �˻縦 �ݺ����� �ʴ´�.

```bash
python3 -I tools/cloud-launch/cloud_launch.py inspect \
  --config "$HOME/.local/share/seokpan-rosa-plan-inputs-5psj2l62/rosa-reconstruction-preparation-0kqodl8m/rosa-lifecycle.json" \
  --receipt /private/new-controller-input-index.json
```

## 2. ���� ���� ��롤���� ���� Ȯ��

`launch-inputs.TEMPLATE.NOT_ACCEPTED.json`�� ��ȣ ��ġ�� �����ϰ� ���� �ڷῡ�� ���� ���� ä���. `352.31`�� A�� �� �ܿ�147.69���� ������ �ȳ� ���ذ��̸� �ǽð� û�� Ȯ�ΰ��� �ƴϴ�. �� �ֽ��� ������ ������ �� ���� ����. �̹ݿ�/������ �������ð��� ���� ������������ ����������/���� �ð� ������ �𸣸� null�� 0���� �ٲ��� �ʴ´�.

```bash
# �� Cluster ���� ��� �־ ��� ����� ���� �����ϴ�. �ڿ� ���� ����.
python3 -I tools/cloud-launch/cloud_launch.py budget \
  --inputs /private/launch-inputs.json --receipt /private/new-budget-estimate.json
```

`���� �ȳ��� + �̹ݿ�/������ ���� + �ð��� ���� ���� �� (����~���� ���� �ð� + ���� �ð� ����) + ������ ����`�� ����450 ��ȹ���� ������ �ߴ��Ѵ�. 500 �ѵ��� �ø��� �ʴ´�. �̴� ���޵� ����ġ�� ���� ����̸� **�ǽð� ��� ���á��ڵ� �������ڵ� ���������� �� �Ϸ� ���� ����� �ƴϴ�.** ���� ���� �ð��� �߰� ������ ���߰�, ���Ÿ� ������ �� �ش� �������� ����/���� �Ǵ��� �����Ѵ�.

## 3. �� ���� ���� �� ���� ���� ���� ���� ����

```bash
python3 -I tools/cloud-launch/cloud_launch.py prepare \
  --source /path/to/pinned-hybrid-gitops \
  --inputs /private/launch-inputs.json \
  --kustomize /path/to/kustomize \
  --out /private/new-launch-package
```

����� `single/shared/target.source.json`, `*.desired.json`, `*.release.json`, `package.json`�̴�. ���� ���뿡�� ConfigMap �ؽÿ� Backend ������ �����ϸ�, ���� Data ��й�ȣ�� CA/Token ���� �аų� ���� �ʴ´�. 13�� ���� ������ �ٲ���ų� ���� Host/�� ID/��ȣ ��ü UID�� ������ �غ� �ߴ��Ѵ�.

## 4. ���� Data ��� �� Application ���� ����

`execution-evidence.TEMPLATE.NOT_ACCEPTED.json`���� ���� ���� ��� ������ ��ġ�� �ִ´�. �� �������� A/D���� �䱸���� �ʴ´�.

- ���� final-data-handoff: ������ ������ ����/����������/Schema�� ���� �� ���� ���� ���.
- ���� post_import_new_cluster ���/target: �̹� �� Cluster�� ���� ���� �б� ����. SQL�� �ٽ� �����Ű�� ������ �ƴϴ�.
- Migration current `20260902_0002`�� ���� �ٰ�. Schema ���̰� ������ upgrade�� �ݺ����� �ʴ´�.
- captured ��ȯ/���� ������ �з��� �ʱ� Pool �ĺ� ������ ���� ����. ���� Redis Ű�� �ϰ� �����ϰų� ��带 ���� �����Ű�� �ʴ´�.

```bash
# ���� ���� ���� ������ ���� ���� �˻縸 �Ѵ�.
python3 -I tools/cloud-launch/cloud_launch.py stage \
  --package /private/new-launch-package --stage single \
  --evidence /private/execution-evidence.json \
  --kubeconfig /private/current-personal-kubeconfig \
  --receipt /private/new-stage-single.json

# ���� �� ������ �����ϱ�� �� �ڿ��� ���� ���ɿ� --execute �߰�.
# --execute�� Application Source ���Ÿ� �ϸ� Sync/�� �⵿�� ���� �ʴ´�.
```

���� ������ API/Namespace����ȣ ��ü4�� UID������ ��ü��App ����/���� ������ Ȯ���ϰ� UID/resourceVersion/spec�� �������� Source�� patch�Ѵ�. ���� kubeconfig�� ������ �ڰ����� ��ü�ϰų� ������ �߰����� �ʴ´�. ���� ǥ�� �� ����/timeout�̸� ���� marker/�α׸� �����ϰ� ������� �����Ѵ�. ���� ������ �� receipt �̸����� �ٲپ� ������ ��õ����� �ʴ´�.

## 5. �� Argo Console���� ��ü ���� �� �� ���� �⵿

stage ���� receipt�� `console_file`�� �� Argo ȭ���� ������ ������ Console�� �ٿ� �ִ´�. �⺻ `SYNC_NOW = false`�� GET �񱳻��̴�. �̹� �� Ȱ�� ������ ���ϴ� ���̸� ���� �� ���� ���� ���� �ݺ��� �ƴϴ�.

���� �ش� �ܰ� �⵿�� ������ �� `SYNC_NOW = true`�� �ٲپ� �����ϰ� Ȯ��â�� �����Ѵ�. �񱳰� �ٸ��� POST���� �ʴ´�. ��ġ�ϸ� ����8�� �Ǵ�10���� Sync ��û�ϸ� prune=false��retry0�̴�. ���� ����� ��û �����̸� ���� Ready/���� ������ �ƴϴ�. 504/���� ��Ȯ�� �� ���������� �ʴ´�. ���� ������ session�� ���� ǥ���� ���´�. ����/�ٸ� ������������ �ߺ� ������� �����ϴ� ��ġ�� �ƴϹǷ� ���� Application operation���� �����Ѵ�.

## 6. ���� Ready Ȯ�ΰ� ���� �Է� ����

```bash
python3 -I tools/cloud-launch/cloud_launch.py readback \
  --package /private/new-launch-package --stage single \
  --kubeconfig /private/current-personal-kubeconfig --execute \
  --receipt /private/new-single-ready.json
```

���� Source/8~10�� inventory��Synced/Healthy��Deployment ����/���� �̹����� ����/���� Replica��Route Admitted�� �д´�. ������� `PASS_CLOUD_STAGE_READY_NOT_BUSINESS_ACCEPTANCE`��. HTTP/������Pool ������WS������ ���¸� �� ��ȸ������ �Ϸ����� �ʴ´�.

��ǥ ���� ������ ������ �� ���� �������� ���� ���� ���� �Է��� �����.

```bash
python3 -I tools/cloud-launch/cloud_launch.py measurement \
  --package /private/new-launch-package --stage single \
  --readback /private/new-single-ready.json \
  --normal-business-ref RUN_ACTUAL_NORMAL_BUSINESS \
  --receipt /private/new-measurement-plan.json

# ���� ���� ���� ������ ���� �Է� �˻�. ������/HTTP ����.
node tools/cloud-measurement/run-cloud-measurement.mjs \
  --plan /private/new-measurement-plan.json

# ���� ���� ���� �ÿ��� --execute --out /private/new-measurement-results �߰�.
```

shared/target�� `--stage`�� �ٲٰ� ���� readback/���� ���� �ٰŸ� execution-evidence�� �����Ѵ�. target�� ���� ����/�ٸ� Pod ������ ������ �߰��Ѵ�. �ܰ躰 �� ������ ����ϸ� ���� ����� ����� �ʴ´�. �̹� ������ �ռ� ȸ���� ���� API�� ���� ���� �� �ִ�.

## ��ǥ�� �켱 �� ����

1. ���� ���� ���� ª�� �ÿ�: �α��Ρ��桤��������ǥ������ ��� �ݿ�. ���� UI �ػ� ���� ����� ����.
2. ���� 6Ŭ���̾�Ʈ/2��: �غ�60��+����180�ʿ��� HTTP p95/ù �õ� ����������ǥ ���ۡ�peer WS/UI p95������ ó������ǥ����/�ڿ� ����. ����/����/�� �غ�/���� �ð��� ��4�� ���̴�.
3. �̹� �ʼ� �����/�������� �ڿ������� ����� ���� �ܰ躰 �ҿ�ð������� �� ���� ��������� ��� ���. �� ��ġ�� �������� �ٽ� ����/��������� �ʴ´�.

12�� �߰�����ð����ִ� ����� �����پ��� ��֡���ü DR�� �켱 ���� ������ �߰����� �ʴ´�. ���� �̿Ϸ� �׸��� PARTIAL/NOT RUN���� ����� ��ü HA/DR/���� ���� �ϷḦ �������� �ʴ´�. ���� ��ǥ ����/�����̵�� �����̸� �� ����� �̹� ��� ���࿡�� ���� Ȯ���� ���ž��̴�.

## ���� �� �ʿ��� �Է¸�

- ���� Foundation �Է�/��¡�Data ���� �ڷ��� ��ȣ ��ġ/����, NAT/RDS/���� ����� ���� �غ� ����. ���� ��û�� �ݺ����� �ʴ´�.
- ���� ��� ���� �ٰſ� ����/���� ���� �ð�, �̹ݿ�/���������� �ð� ����.
- �� ROSA ID/API/Ingress��Namespace/Secret/CA UID������ kubeconfig. �ּҴ� ���⼭ �ڵ� �����Ѵ�.
- ���� Data/���� SQL/Migration current��captured ��ȯ/Pool ���ð� �ܰ躰 ���� ���� ����� ���� ����/�ٰ�.

Ű/��й�ȣ/Token�� ä���̳� Git�� ������ �ʴ´�. ���� �غ�� ���� ��������� �⵿��HTTP/WSS��������� ��� ������ ���� ���Ű� �ƴϴ�.
