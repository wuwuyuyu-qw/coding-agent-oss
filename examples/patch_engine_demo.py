"""Minimal Patch Engine demo using a temporary copy of examples/buggy_project."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from patching.applier import PatchApplier
from patching.diff_audit import DiffAuditor
from patching.parser import PatchParser
from patching.validator import PatchValidator


RAW_PATCH = """```search
def divide(a: int, b: int) -> float:
    return a / b
```
```replace
def divide(a: int, b: int) -> float:
    if b == 0:
        return 0
    return a / b
```"""


def main() -> None:
    source = PROJECT_ROOT / "examples" / "buggy_project"
    with tempfile.TemporaryDirectory(prefix="patch-engine-demo-") as temp_dir:
        repo_root = Path(temp_dir) / "buggy_project"
        shutil.copytree(source, repo_root)
        _init_git(repo_root)

        parser = PatchParser()
        plan = parser.parse(RAW_PATCH, repo_root=str(repo_root), default_target_file="calculator.py")
        validation = PatchValidator().validate(plan, repo_root=str(repo_root))
        dry_run = PatchApplier().dry_run(plan, repo_root=str(repo_root))
        apply_result = PatchApplier().apply(plan, repo_root=str(repo_root))
        audit = DiffAuditor().audit(
            repo_root=str(repo_root),
            patch_id=plan.patch_id,
            files_before_hash=apply_result.metadata.get("files_before_hash", {}),
            files_after_hash=apply_result.metadata.get("files_after_hash", {}),
        )

        print("Patch Engine Demo")
        print(f"patch_type: {plan.patch_type.value}")
        print(f"target_files: {plan.target_files}")
        print(f"validation_valid: {validation.valid}")
        print(f"dry_run_success: {dry_run.success}")
        print(f"apply_success: {apply_result.success}")
        print(f"changed_files: {audit.changed_files}")
        print(f"line_added: {audit.line_added}")
        print(f"line_deleted: {audit.line_deleted}")


def _init_git(repo_root: Path) -> None:
    _git(repo_root, "init")
    _git(repo_root, "config", "user.email", "demo@example.com")
    _git(repo_root, "config", "user.name", "Demo User")
    _git(repo_root, "add", ".")
    _git(repo_root, "commit", "-m", "demo baseline")


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo_root, check=True, capture_output=True, text=True, shell=False)


if __name__ == "__main__":
    main()

