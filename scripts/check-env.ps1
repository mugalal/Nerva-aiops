#Requires -Version 5.1
# Day-1 laptop check (Windows). Run: powershell -ExecutionPolicy Bypass -File scripts\check-env.ps1
$fail = $false
function Check($name, [scriptblock]$cmd, $hint) {
  try { $v = & $cmd 2>&1 | Out-String; Write-Output "[OK] $name : $($v.Split("`n")[0].Trim())" }
  catch { Write-Output "[MISSING] $name -- $hint"; $script:fail = $true }
}
Check "python 3.11+" { (python --version) } "Install Python 3.11 from python.org, tick 'Add to PATH'"
Check "git" { (git --version) } "Install Git for Windows"
Check "docker" { (docker --version) } "Install Docker Desktop, enable it"
Check "kubectl" { (kubectl version --client --short=true) } "Needed on demo machine (M4): winget install Kubernetes.kubectl"
Check "kind" { (kind --version) } "Needed on demo machine (M4): winget install Kubernetes.kind (or minikube)"
if ($fail) { Write-Output "`nRESULT: install the [MISSING] tools today (Day 1), not Day 4."; exit 1 }
else { Write-Output "`nRESULT: all required tools present."; exit 0 }
