"""Check PowerShell argument forwarding with a local argv recorder, without kubectl."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHELLS = list(dict.fromkeys(filter(None, (
    shutil.which("powershell.exe"), shutil.which("pwsh.exe"),
))))
HARNESS = r"""param([string]$RepoRoot, [string]$ProbePython, [string]$CaptureScript, [string]$Case)
$ErrorActionPreference = 'Stop'
$taskKubectl = $ProbePython
$taskKubeconfig = 'C:\fake folder\config.yaml'
if ($Case -eq 'script') {
    $taskCode = Get-Content -LiteralPath (Join-Path $RepoRoot 'scripts/rebuild-spike-modules.ps1') -Raw
    $taskNames = @('Invoke-RebuildKube', 'Assert-NoActiveRecovery')
} else {
    $taskGuideFile = if ($Case -in @('baseline', 'incidents')) { 'docs/COMMANDS_TRAFFIC_SPIKE_REVIEWED.md' } else { 'docs/TEST_TRAFFIC_SPIKE_SELF_SERVICE_WINDOWS.md' }
    $taskGuide = Get-Content -LiteralPath (Join-Path $RepoRoot $taskGuideFile) -Raw
    $taskCode = ([regex]::Matches($taskGuide, '(?s)```powershell\r?\n(.*?)\r?\n```') |
        Where-Object { $_.Groups[1].Value -match 'function D' } |
        Select-Object -First 1).Groups[1].Value
    $taskNames = if ($Case -eq 'incidents') { @('Get-TaskIncidents') } else { @('K', 'D') }
}
$taskTokens = $null
$taskErrors = $null
$taskAst = [System.Management.Automation.Language.Parser]::ParseInput($taskCode, [ref]$taskTokens, [ref]$taskErrors)
if ($taskErrors.Count) { throw 'PowerShell source contains a parse error.' }
foreach ($taskName in $taskNames) {
    $taskDefinitions = $taskAst.FindAll({param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $taskName
    }, $true)
    if ($taskDefinitions.Count -ne 1) { throw "Missing function: $taskName" }
    # Replace only the native executable invocation; retain the real forwarding logic and callers.
    $taskDefinition = $taskDefinitions[0].Extent.Text.Replace(
        '& $taskKubectl @taskArguments @args', '& $ProbePython $CaptureScript @taskArguments @args')
    $taskDefinition = $taskDefinition.Replace(
        '& $taskKubectl @taskFlags @args', '& $ProbePython $CaptureScript @taskFlags @args')
    Invoke-Expression $taskDefinition
}
if ($Case -eq 'script') {
    Assert-NoActiveRecovery
} elseif ($Case -eq 'incidents') {
    $taskM4 = 'http://fake.invalid'
    function Invoke-RestMethod {
        param([string]$Uri)
        ConvertFrom-Json -InputObject '[{"incident_id":"old","status":"ESCALATED"},{"incident_id":"new","status":"EXECUTING"}]'
    }
    $taskIncidents = @(Get-TaskIncidents)
    $taskActive = @($taskIncidents | Where-Object { $_.status -in @('EXECUTING', 'VALIDATING') })
    if ($taskIncidents.Count -ne 2 -or $taskActive.Count -ne 1 -or $taskActive[0].incident_id -ne 'new') { throw 'Active recovery guard failed to enumerate incidents.' }
    $taskIncidents | ConvertTo-Json -Depth 5 -Compress
} elseif ($Case -eq 'baseline') {
    $taskBaseline = @(D baseline)
    if ($taskBaseline.Count -ne 2 -or $taskBaseline[0] -ne 200 -or $taskBaseline[1].version -ne 'v1' -or $taskBaseline[1].replica_count.minimum -ne 1 -or $taskBaseline[1].replica_count.maximum -ne 1) {
        throw 'Healthy JSON baseline was incorrectly rejected.'
    }
    $taskBaseline | ConvertTo-Json -Depth 5 -Compress
} else {
    D baseline | ConvertTo-Json -Depth 5 -Compress
}
"""


@unittest.skipUnless(SHELLS, "PowerShell is required")
class KubectlArgumentForwardingTests(unittest.TestCase):
    def run_probe(self, shell, case):
        with tempfile.TemporaryDirectory(prefix="nerva-argv-") as directory:
            layout = Path(directory)
            capture = layout / "capture.py"
            if case == "baseline":
                capture.write_text(
                    "import json,sys\nsys.stdin.read()\nprint(json.dumps([200, {'version':'v1', 'replica_count':{'minimum':1.0, 'maximum':1.0}}]))\n",
                    encoding="utf-8",
                )
            else:
                capture.write_text(
                    "import json,sys\nprint(json.dumps({'argv':sys.argv[1:],'stdin':sys.stdin.read()}))\n",
                    encoding="utf-8",
                )
            harness = layout / "probe.ps1"
            harness.write_text(HARNESS, encoding="utf-8")
            (layout / "scripts").mkdir()
            (layout / "scripts/live-drill.py").write_text("fake probe input\n", encoding="utf-8")
            environment = {
                name: os.environ[name]
                for name in ("SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP", "PATH", "PATHEXT")
                if name in os.environ
            }
            result = subprocess.run(
                [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness),
                 "-RepoRoot", str(ROOT), "-ProbePython", sys.executable,
                 "-CaptureScript", str(capture), "-Case", case],
                cwd=layout, env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def test_rebuild_guard_preserves_exec_separator_and_piped_python(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                result = self.run_probe(shell, "script")
                self.assertEqual(result["argv"], [
                    "--kubeconfig", r"C:\fake folder\config.yaml", "--context", "kind-nexus",
                    "-n", "nexus-demo", "exec", "-i", "deployment/shared-nexus-api", "--", "python", "-",
                ])
                self.assertIn("import json, urllib.request", result["stdin"])
                self.assertIn("EXECUTING", result["stdin"])

    def test_spike_guide_preserves_separator_stdin_and_baseline_argument(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                result = self.run_probe(shell, "guide")
                self.assertEqual(result["argv"], [
                    "--kubeconfig", r"C:\fake folder\config.yaml", "--context", "kind-nexus",
                    "-n", "nexus-demo", "exec", "-i", "deployment/shared-nexus-api", "--", "python", "-", "baseline",
                ])
                self.assertEqual(result["stdin"].strip(), "fake probe input")

    def test_reviewed_guide_accepts_healthy_baseline_json_array(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                result = self.run_probe(shell, "baseline")
                self.assertEqual(result[0], 200)
                self.assertEqual(result[1]["version"], "v1")
                self.assertEqual(result[1]["replica_count"], {"minimum": 1.0, "maximum": 1.0})

    def test_reviewed_guide_enumerates_incidents_for_active_recovery_guard(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                result = self.run_probe(shell, "incidents")
                self.assertEqual([item["incident_id"] for item in result], ["old", "new"])


if __name__ == "__main__":
    unittest.main()
