"""Package the APEX project as a zip (used by the app's download button and for releases).

Run ``python bundle.py`` to write APEX_loan_decision_support.zip next to this file.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ZIP_NAME = "APEX_loan_decision_support.zip"
# Only reviewed source files are shipped. A new file in the project directory
# must be deliberately added here before it can appear in a downloadable ZIP.
PACKAGE_FILES = (
    ".gitignore", ".streamlit/config.toml", "README.md", "advisor.py", "app.py", "approval_model.py",
    "bundle.py", "calculations.py", "eligibility.py", "engine.py", "install_and_run.bat",
    "install_and_run.sh", "kyc.py", "models.py", "policy_rules.py", "predictors.py",
    "pricing.py", "profiles.py", "pytest.ini", "reports.py", "requirements.txt", "scoring.py",
    "synthetic_history.py", "config/_defaults.yaml", "config/bank_a_conservative.yaml",
    "config/bank_b_moderate.yaml", "config/bank_c_flexible.yaml",
    "docs/APEX_HANDBOOK.md", "docs/MATHEMATICAL_MODEL.md",
    "tests/conftest.py", "tests/test_advisor.py", "tests/test_bundle.py",
    "tests/test_calculations.py", "tests/test_eligibility_and_config.py",
    "tests/test_extensions.py", "tests/test_pricing.py", "tests/test_profiles.py",
    "tests/test_scoring_and_logic.py",
)
EXECUTABLE = {"install_and_run.sh"}


def project_files(root: Path = ROOT) -> list[Path]:
    root = Path(root)
    files = [root / name for name in PACKAGE_FILES]
    resolved_root = root.resolve()
    missing = [name for name, path in zip(PACKAGE_FILES, files)
               if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(resolved_root)]
    if missing:
        raise FileNotFoundError(f"Package manifest entries missing or unsafe: {', '.join(missing)}")
    return sorted(files)


def project_zip(root: Path = ROOT, folder: str = "APEX") -> bytes:
    """Zip only the reviewed manifest under ``folder/`` with portable paths."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in project_files(root):
            info = zipfile.ZipInfo.from_file(p, f"{folder}/{p.relative_to(root).as_posix()}")
            info.compress_type = zipfile.ZIP_DEFLATED
            if p.name in EXECUTABLE:
                info.external_attr = 0o100755 << 16
            z.writestr(info, p.read_bytes())
    return buf.getvalue()


if __name__ == "__main__":
    out = ROOT / ZIP_NAME
    out.write_bytes(project_zip())
    print(f"Wrote {out} ({out.stat().st_size / 1024:.0f} KB, {len(project_files())} files)")
