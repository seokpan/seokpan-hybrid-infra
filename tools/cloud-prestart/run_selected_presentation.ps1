param([switch]$Execute)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$audit = 'C:\seokpan_aws\.audit\20261011-requirements-audit'
$pointer = Join-Path $audit 'presentation-current-local.json'
$started = $false
$local = $null
function Need($ok, $code) { if (-not $ok) { throw $code } }
function Write-Json($path, $value) {
  [IO.File]::WriteAllText($path, ($value | ConvertTo-Json -Depth 100), [Text.UTF8Encoding]::new($false))
}
try {
  Need $Execute 'EXPLICIT_PRESENTATION_EXECUTION_REQUIRED'
  Need ($PSVersionTable.PSVersion.Major -ge 7) 'POWERSHELL_7_REQUIRED'
  Remove-Item Env:DEBUG,Env:PWDEBUG -ErrorAction SilentlyContinue
  # Controller에서 출력한 최종 성공 JSON만 클립보드에 복사해 둡니다.
  $receipt = Get-Clipboard -Raw | ConvertFrom-Json
  Need ($receipt.status -eq 'PASS_SELECTED_PRESENTATION_INPUTS_READY_NOT_TESTED') 'CONTROLLER_PREPARATION_SUCCESS_REQUIRED'
  Need ($receipt.binding.cluster_id -eq '2ti8n9c2j2em1h6jbb34kc0dq95dv4fi') 'SAME_CLUSTER_REQUIRED'
  Need ($receipt.binding.application_uid -eq 'a628168b-2a30-4de5-b5d3-809f74853d15') 'SAME_APPLICATION_REQUIRED'
  Need ($receipt.measurement_bundle.baseline.runtime.replicas.backend -eq 3 -and $receipt.measurement_bundle.baseline.runtime.replicas.frontend -eq 3) 'THREE_BY_THREE_PROFILE_REQUIRED'
  Need ([DateTimeOffset]::Parse($receipt.window.not_after) -gt [DateTimeOffset]::Now.AddMinutes(12)) 'SUFFICIENT_CURRENT_TEST_WINDOW_REQUIRED'
  $paths = Get-Content -Raw -Encoding UTF8 (Join-Path $audit 'B_DEFAULT_GENERATOR_EXECUTION_PATHS_NOT_ACCEPTED_20261019.json') | ConvertFrom-Json
  $node = $paths.observer_node
  $observer = Join-Path $audit 'fresh-test-launch-20261020\tools\presentation-measurement\run-observer.mjs'
  Need ((Get-FileHash -LiteralPath $observer -Algorithm SHA256).Hash.ToLowerInvariant() -eq 'fd4e972debad96cb5c2b2716949008753cf246514439ecc03057d83e40be26c8') 'EXISTING_APPROVED_OBSERVER_REQUIRED'
  Need ((Test-Path -LiteralPath $node -PathType Leaf) -and (Test-Path -LiteralPath $paths.observer_playwright_module -PathType Leaf)) 'EXISTING_GENERATOR_RUNTIME_REQUIRED_NO_INSTALL'
  $runId = $receipt.measurement_bundle.baseline.run_id -replace '-baseline$', ''
  Need ($runId -match '^b-presentation-[0-9]{8}-[0-9]{6}$') 'SAFE_PREPARED_RUN_ID_REQUIRED'
  $local = Join-Path $audit $runId
  Need (-not (Test-Path -LiteralPath $local)) 'EXISTING_RUN_PRESERVED_NO_AUTOMATIC_RETRY'
  New-Item -ItemType Directory -Path $local | Out-Null
  Write-Json (Join-Path $local 'controller-preparation.json') $receipt
  Write-Json (Join-Path $local 'baseline.json') $receipt.measurement_bundle.baseline
  Write-Json (Join-Path $local 'rolling.json') $receipt.measurement_bundle.rolling
  $remote = @(
    $paths.controller_python, '-I',
    ($paths.controller_measurement_dir + '/rolling_control.py'),
    'trigger', '--config', ($paths.controller_work + '/rolling-config.json'),
    '--work', ($paths.controller_work + '/rolling-work'), '--execute'
  ) -join ' '
  $trigger = @{scope='ONE_BACKEND_ROLL';binding=$receipt.binding;argv=@($paths.controller_trigger_prefix) + @($remote)}
  Write-Json (Join-Path $local 'trigger.json') $trigger
  $abort = Join-Path $local 'ABORT'
  Write-Json $pointer @{local_directory=$local;abort_file=$abort;status='PREPARED_NOT_STARTED';run_id=$runId}
  foreach ($scenario in @('baseline','rolling')) {
    Need (-not (Test-Path -LiteralPath $abort)) 'ABORT_REQUESTED_NO_NEXT_SCENARIO'
    Need ([DateTimeOffset]::Parse($receipt.window.not_after) -gt [DateTimeOffset]::Now.AddMinutes(9)) 'INSUFFICIENT_REMAINING_WINDOW_NO_NEXT_SCENARIO'
    # 정상 시험 성공 뒤에만 롤링 시험을 시작합니다.
    $arguments = @($observer,'--plan',(Join-Path $local ($scenario + '.json')),
      '--out',(Join-Path $local $scenario),'--playwright-module',$paths.observer_playwright_module,
      '--abort-file',$abort,'--execute')
    if ($scenario -eq 'rolling') { $arguments += @('--trigger',(Join-Path $local 'trigger.json')) }
    $psi = [Diagnostics.ProcessStartInfo]::new()
    $psi.FileName=$node; $psi.UseShellExecute=$false; $psi.CreateNoWindow=$true
    foreach ($argument in $arguments) { $psi.ArgumentList.Add([string]$argument) }
    Write-Json $pointer @{local_directory=$local;abort_file=$abort;status=($scenario.ToUpperInvariant() + '_STARTED');run_id=$runId;utc=[DateTimeOffset]::UtcNow.ToString('o')}
    $process = [Diagnostics.Process]::new(); $process.StartInfo=$psi
    Need ($process.Start()) 'GENERATOR_START_FAILED'
    $started = $true
    $timer=[Diagnostics.Stopwatch]::StartNew(); $abortSince=$null
    while (-not $process.WaitForExit(1000)) {
      if (Test-Path -LiteralPath $abort) {
        if ($null -eq $abortSince) { $abortSince=$timer.Elapsed.TotalSeconds }
        if (($timer.Elapsed.TotalSeconds-$abortSince) -gt 45) { $process.Kill($true); throw 'GENERATOR_ABORTED_PARTIAL_EVIDENCE_ONLY' }
      }
      if ($timer.Elapsed.TotalSeconds -gt 540) { $process.Kill($true); throw 'GENERATOR_TIMEOUT_RECONCILE_NO_RETRY' }
    }
    Need ($process.ExitCode -eq 0) 'SCENARIO_INCOMPLETE_NO_NEXT_SCENARIO'
    $result = Get-Content -Raw -Encoding UTF8 (Join-Path $local ($scenario + '\receipt.json')) | ConvertFrom-Json
    Need ($result.status -eq 'COMPLETED_BOUNDED_FAULT_OBSERVATION_NOT_ACCEPTANCE') 'BOUNDED_OBSERVATION_INCOMPLETE'
    Write-Json (Join-Path $local ($scenario + '.operator-result.json')) @{utc=[DateTimeOffset]::UtcNow.ToString('o');status=$result.status;scenario=$scenario}
  }
  Write-Json $pointer @{local_directory=$local;abort_file=$abort;status='TWO_OBSERVATIONS_COMPLETED_PENDING_REVIEW';run_id=$runId}
  $archive=$local + '.zip'
  Compress-Archive -LiteralPath $local -DestinationPath $archive
  @{status='TWO_OBSERVATIONS_COMPLETED_PENDING_REVIEW';local_directory=$local;archive=$archive;sha256=(Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant();acceptance='REQUIRES_COUNTS_WSS_AND_CONTROLLER_TIMELINE_REVIEW'} | ConvertTo-Json
} catch {
  $reason=$_.Exception.Message
  if ($reason -notmatch '^[A-Z0-9_]+$') { $reason='EXECUTION_FAILED_NO_RAW_CREDENTIAL_OUTPUT' }
  $result=@{status='STOP_SELECTED_PRESENTATION';reason=$reason;generator_started=$started;local_directory=$local;automatic_retry=$false}
  if ($null -ne $local -and (Test-Path -LiteralPath $local)) { Write-Json (Join-Path $local 'operator-stop.json') $result }
  $result | ConvertTo-Json
  exit 2
}
