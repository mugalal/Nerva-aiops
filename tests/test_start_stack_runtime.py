"""Run the real startup script with isolated settings and a fake Docker command."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
RUNTIME_NAMES = (
    "NEXUS_DB_PASSWORD",
    "NEXUS_APPROVAL_TOKEN",
    "NEXUS_INGEST_TOKEN",
    "M5_UI_OPERATOR_TOKEN",
    "M5_OPERATOR_ID",
    "GEMINI_API_KEY",
    "M5_LLM_MODEL",
)


def prepare_isolated_stack(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/start-stack.ps1", tmp_path / "scripts/start-stack.ps1")
    (tmp_path / "observability").mkdir()
    (tmp_path / "observability/docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    environment_lines = "\n".join(f"            {name} = $env:{name}" for name in RUNTIME_NAMES)
    (tmp_path / "run.ps1").write_text(
        """$ErrorActionPreference = 'Stop'
function global:docker {
    [pscustomobject]@{
        arguments = @($args)
        environment = [ordered]@{
"""
        + environment_lines
        + """
        }
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'docker-call.json') -Encoding utf8
    $global:LASTEXITCODE = 0
}
& (Join-Path $PSScriptRoot 'scripts/start-stack.ps1') -NoBuild
""",
        encoding="utf-8",
    )
    return tmp_path


def run_stack(layout, overrides=None):
    # Do not inherit runtime credentials from the developer's environment.
    environment = {
        name: os.environ[name]
        for name in ("SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP")
        if name in os.environ
    }
    environment.update(overrides or {})
    completed = subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(layout / "run.ps1")],
        cwd=layout,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    runtime = json.loads((layout / ".review-branches/stack-runtime.json").read_text(encoding="utf-8-sig"))
    docker_call = json.loads((layout / "docker-call.json").read_text(encoding="utf-8-sig"))
    assert docker_call["arguments"] == [
        "compose", "-f", str(layout / "observability/docker-compose.yml"),
        "--profile", "integration", "up", "--detach", "--wait", "--wait-timeout", "300",
    ]
    assert docker_call["environment"] == {name: runtime.get(name) for name in RUNTIME_NAMES}
    return runtime


@unittest.skipUnless(POWERSHELL, "PowerShell is required")
class StartStackRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="start-stack-runtime-")
        self.addCleanup(temporary.cleanup)
        self.layout = prepare_isolated_stack(Path(temporary.name))

    def test_first_use_persists_gemini_and_model_then_restores_without_rotating_tokens(self):
        first = run_stack(self.layout, {
            "GEMINI_API_KEY": "fake-first-use-gemini-key",
            "M5_LLM_MODEL": "fake-custom-model",
            "M5_OPERATOR_ID": "fake-first-operator",
        })
        self.assertEqual(first["GEMINI_API_KEY"], "fake-first-use-gemini-key")
        self.assertEqual(first["M5_LLM_MODEL"], "fake-custom-model")
        self.assertEqual(first["M5_OPERATOR_ID"], "fake-first-operator")
        self.assertTrue(all(first[name] for name in RUNTIME_NAMES))

        # A fresh process has no Gemini/model/token overrides and must reuse saved values.
        self.assertEqual(run_stack(self.layout), first)

    def test_existing_runtime_settings_survive_and_explicit_environment_overrides_persist(self):
        existing = {
            "NEXUS_DB_PASSWORD": "saved_db_password_0123456789",
            "NEXUS_APPROVAL_TOKEN": "saved_approval_" + "a" * 32,
            "NEXUS_INGEST_TOKEN": "saved_ingest_" + "i" * 32,
            "M5_UI_OPERATOR_TOKEN": "saved_ui_" + "u" * 32,
            "M5_OPERATOR_ID": "saved-operator",
            "custom_setting": "preserve-this-value",
        }
        runtime_path = self.layout / ".review-branches/stack-runtime.json"
        runtime_path.parent.mkdir()
        runtime_path.write_text(json.dumps(existing), encoding="utf-8")
        configured = run_stack(self.layout, {
            "GEMINI_API_KEY": "fake-existing-runtime-gemini-key",
            "M5_LLM_MODEL": "fake-existing-runtime-model",
        })
        self.assertTrue(all(configured[name] == value for name, value in existing.items()))
        self.assertEqual(run_stack(self.layout), configured)

        overrides = {
            "NEXUS_DB_PASSWORD": "override_db_password_0123456789",
            "NEXUS_APPROVAL_TOKEN": "override_approval_" + "a" * 32,
            "NEXUS_INGEST_TOKEN": "override_ingest_" + "i" * 32,
            "M5_UI_OPERATOR_TOKEN": "override_ui_" + "u" * 32,
            "M5_OPERATOR_ID": "override-operator",
            "GEMINI_API_KEY": "fake-replacement-gemini-key",
            "M5_LLM_MODEL": "fake-replacement-model",
        }
        replaced = run_stack(self.layout, overrides)
        self.assertEqual(replaced, {**configured, **overrides})
        self.assertEqual(run_stack(self.layout), replaced)


if __name__ == "__main__":
    unittest.main()
