"""The downloadable project bundle."""
import io
import zipfile

import bundle


def test_bundle_contains_project_and_excludes_local_state():
    data = bundle.project_zip()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        assert z.testzip() is None
        sh = z.getinfo("APEX/install_and_run.sh")
    for must in ("APEX/.gitignore", "APEX/app.py", "APEX/advisor.py", "APEX/age_math.py", "APEX/data_lab.py",
                 "APEX/demo_check.py",
                 "APEX/requirements.txt", "APEX/requirements-dev.txt", "APEX/install_and_run.bat",
                 "APEX/install_and_run.sh", "APEX/docs/APEX_HANDBOOK.md", "APEX/config/_defaults.yaml",
                 "APEX/tests/test_advisor.py", "APEX/.streamlit/config.toml"):
        assert must in names
    assert all("\\" not in n for n in names)
    assert not any(part in n for n in names for part in (".venv/", "__pycache__", "model_store/", "overlays/", ".zip"))
    assert (sh.external_attr >> 16) & 0o111


def test_bundle_only_packages_manifest_files(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "PACKAGE_FILES", ("README.md",))
    (tmp_path / "README.md").write_text("public project notes")
    (tmp_path / "customer_data.csv").write_text("private applicant data")
    with zipfile.ZipFile(io.BytesIO(bundle.project_zip(tmp_path))) as z:
        assert z.namelist() == ["APEX/README.md"]


def test_bundle_rejects_symlink_outside_project(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "PACKAGE_FILES", ("README.md",))
    outside = tmp_path.parent / "outside_project.txt"
    outside.write_text("private")
    (tmp_path / "README.md").symlink_to(outside)
    try:
        bundle.project_zip(tmp_path)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("bundle followed a symlink outside the project")
