import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from pyifc.cli import main

APP = textwrap.dedent('''
    from pyifc import Application, Bucket

    app = Application("tiny")
    {name} = Bucket("{name}")


    @app.handler(method="POST", path="/records", id="ingest")
    @app.write({name})
    def ingest(body, content_type):
        {name}.put("k", body)
        return {{"ok": True}}
''')

APP_WITH_LIST = textwrap.dedent('''
    from pyifc import Application, Bucket

    app = Application("tiny")
    {name} = Bucket("{name}")


    @app.handler(method="POST", path="/records", id="ingest")
    @app.write({name})
    @app.list({name})
    def ingest(body, content_type):
        {name}.put("k", body)
        {name}.list("")
        return {{"ok": True}}
''')

UNDECLARED = textwrap.dedent('''
    from pyifc import Application, Bucket

    app = Application("tiny")
    records = Bucket("records")


    @app.handler(method="POST", path="/records", id="ingest")
    def ingest(body, content_type):
        records.put("k", body)
        return {}
''')


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    return root


def write_app(project: Path, text: str) -> Path:
    path = project / "app.py"
    path.write_text(text)
    return path


def generate(app, out, *extra):
    return main(["generate", str(app), "--out", str(out), *extra])


def test_check_reports_undeclared_use_and_exits_1(project, capsys):
    app = write_app(project, UNDECLARED)

    assert main(["check", str(app)]) == 1

    err = capsys.readouterr().err
    assert "E_UNDECLARED_USE" in err
    assert "app.py:" in err


def test_check_clean_app_exits_0(project, capsys):
    app = write_app(project, APP.format(name="records"))

    assert main(["check", str(app)]) == 0
    assert capsys.readouterr().err == ""


def test_graph_twice_gives_identical_bytes(project, tmp_path):
    app = write_app(project, APP.format(name="records"))
    first, second = tmp_path / "a.json", tmp_path / "b.json"

    assert main(["graph", str(app), "-o", str(first)]) == 0
    assert main(["graph", str(app), "-o", str(second)]) == 0

    assert first.read_bytes() == second.read_bytes()
    assert json.loads(first.read_text())["name"] == "tiny"


def test_graph_refuses_when_analysis_has_errors(project, tmp_path):
    app = write_app(project, UNDECLARED)
    out = tmp_path / "g.json"

    assert main(["graph", str(app), "-o", str(out)]) == 1
    assert not out.exists()


def test_generate_without_lock_exits_2(project, tmp_path, capsys):
    app = write_app(project, APP.format(name="records"))
    out = tmp_path / "out"

    assert generate(app, out) == 2

    assert "E_LOCK_MISSING" in capsys.readouterr().err
    assert not (out / "app.tf.json").exists()
    assert (out / "diagnostics.json").exists()
    assert not (project / "pyifc.lock.json").exists()


def test_generate_init_lock_writes_terraform_diagnostics_and_lock(project, tmp_path):
    app = write_app(project, APP.format(name="records"))
    out = tmp_path / "out"

    assert generate(app, out, "--init-lock") == 0

    document = json.loads((out / "app.tf.json").read_text())
    assert document["resource"]["aws_s3_bucket"]["records"]["bucket"] == "${var.name_prefix}-records"
    lock = json.loads((project / "pyifc.lock.json").read_text())
    assert lock["resources"]["records"]["physical_name"] == "${var.name_prefix}-records"
    diagnostics = json.loads((out / "diagnostics.json").read_text())
    assert isinstance(diagnostics, list)


def test_lock_option_overrides_location(project, tmp_path):
    app = write_app(project, APP.format(name="records"))
    lock = tmp_path / "elsewhere" / "my.lock.json"
    lock.parent.mkdir()

    assert generate(app, tmp_path / "out", "--init-lock", "--lock", str(lock)) == 0

    assert lock.exists()
    assert not (project / "pyifc.lock.json").exists()


def test_inferred_rename_keeps_the_physical_name_and_adds_moved(project, tmp_path, capsys):
    app = write_app(project, APP.format(name="records"))
    out = tmp_path / "out"
    assert generate(app, out, "--init-lock") == 0
    capsys.readouterr()

    write_app(project, APP.format(name="archive"))
    assert generate(app, out) == 0

    assert "W_RENAME_INFERRED" in capsys.readouterr().err
    document = json.loads((out / "app.tf.json").read_text())
    assert document["resource"]["aws_s3_bucket"]["archive"]["bucket"] == "${var.name_prefix}-records"
    assert {"from": "aws_s3_bucket.records", "to": "aws_s3_bucket.archive"} in document["moved"]
    lock = json.loads((project / "pyifc.lock.json").read_text())
    assert list(lock["resources"]) == ["archive"]
    diagnostics = json.loads((out / "diagnostics.json").read_text())
    assert any(item["code"] == "W_RENAME_INFERRED" for item in diagnostics)


def test_ambiguous_rename_refuses_and_leaves_outputs_untouched(project, tmp_path, capsys):
    app = write_app(project, APP.format(name="records"))
    out = tmp_path / "out"
    assert generate(app, out, "--init-lock") == 0
    terraform = (out / "app.tf.json").read_bytes()
    lock = (project / "pyifc.lock.json").read_bytes()
    capsys.readouterr()

    write_app(project, APP_WITH_LIST.format(name="archive"))
    assert generate(app, out) == 2

    assert "E_RENAME_AMBIGUOUS" in capsys.readouterr().err
    assert (out / "app.tf.json").read_bytes() == terraform
    assert (project / "pyifc.lock.json").read_bytes() == lock
    diagnostics = json.loads((out / "diagnostics.json").read_text())
    assert any(item["code"] == "E_RENAME_AMBIGUOUS" for item in diagnostics)


def test_refusal_does_not_create_terraform_on_first_run(project, tmp_path):
    app = write_app(project, UNDECLARED)
    out = tmp_path / "out"

    assert generate(app, out, "--init-lock") == 1

    assert not (out / "app.tf.json").exists()
    assert not (project / "pyifc.lock.json").exists()


def test_overlay_is_never_modified(project, tmp_path):
    app = write_app(project, APP.format(name="records"))
    out = tmp_path / "out"
    out.mkdir()
    overlay = out / "overlay.tf.json"
    overlay.write_text('{"resource": {}}\n')
    before = overlay.read_bytes()

    assert generate(app, out, "--init-lock") == 0
    assert generate(app, out) == 0

    assert overlay.read_bytes() == before


def test_corrupt_lock_exits_2_naming_the_file(project, tmp_path, capsys):
    app = write_app(project, APP.format(name="records"))
    (project / "pyifc.lock.json").write_text("{not json")

    assert generate(app, tmp_path / "out") == 2

    assert "pyifc.lock.json" in capsys.readouterr().err
    assert not (tmp_path / "out" / "app.tf.json").exists()


def test_destroy_removes_a_stateful_resource_from_the_lock(project, tmp_path):
    app = write_app(project, APP.format(name="records"))
    out = tmp_path / "out"
    assert generate(app, out, "--init-lock") == 0
    write_app(project, APP.format(name="records") + "\nextra = Bucket('extra')\n")

    assert main(["check", str(app)]) == 0  # W_UNUSED_RESOURCE is a warning only
    assert generate(app, out) == 0
    write_app(project, APP.format(name="records"))
    assert generate(app, out) == 2  # extra is stateful and removed
    assert generate(app, out, "--destroy", "extra") == 0
    assert list(json.loads((project / "pyifc.lock.json").read_text())["resources"]) == ["records"]


def test_package_hash_is_the_base64_sha256_of_the_file(project, tmp_path):
    import base64
    import hashlib

    app = write_app(project, APP.format(name="records"))
    package = tmp_path / "pkg.zip"
    package.write_bytes(b"zip bytes")
    out = tmp_path / "out"

    assert generate(app, out, "--init-lock", "--package", str(package)) == 0

    expected = base64.b64encode(hashlib.sha256(b"zip bytes").digest()).decode()
    text = (out / "app.tf.json").read_text()
    assert expected in text


def test_pyifc_toml_supplies_defaults_and_flags_override(project, tmp_path):
    app = write_app(project, APP.format(name="records"))
    (project / "pyifc.toml").write_text('name_prefix = "acme"\nregion = "eu-west-1"\n')
    out = tmp_path / "out"

    assert generate(app, out, "--init-lock") == 0
    document = json.loads((out / "app.tf.json").read_text())
    assert document["variable"]["name_prefix"]["default"] == "acme"
    assert document["provider"]["aws"]["region"] == "eu-west-1"


def test_endpoint_flag_reaches_the_provider(project, tmp_path):
    app = write_app(project, APP.format(name="records"))
    (project / "pyifc.toml").write_text('endpoint = "http://from-toml:1"\n')
    out = tmp_path / "out"

    assert generate(app, out, "--init-lock", "--endpoint", "http://flag:2") == 0
    text = (out / "app.tf.json").read_text()
    assert "http://flag:2" in text
    assert "http://from-toml:1" not in text


def test_iam_mode_declared_is_accepted(project, tmp_path):
    app = write_app(project, APP.format(name="records"))

    assert generate(app, tmp_path / "out", "--init-lock", "--iam-mode", "declared") == 0


def test_run_executes_a_smoke_script_and_returns_its_exit_code(project, tmp_path):
    app = write_app(project, APP.format(name="records"))
    smoke = tmp_path / "smoke.py"
    smoke.write_text(textwrap.dedent('''
        def main(app):
            status, _ = app.call_http("POST", "/records", b"{}")
            return 0 if status == 202 else 3
    '''))
    state = tmp_path / "state"

    assert main(["run", str(app), "--state", str(state), "--smoke", str(smoke)]) == 0
    assert (state / "buckets" / "records" / "k").read_bytes() == b"{}"


def test_run_checks_first_unless_no_check(project, tmp_path, capsys):
    app = write_app(project, UNDECLARED)
    smoke = tmp_path / "smoke.py"
    smoke.write_text("def main(app):\n    return 0\n")
    state = tmp_path / "state"

    assert main(["run", str(app), "--state", str(state), "--smoke", str(smoke)]) == 1
    assert "E_UNDECLARED_USE" in capsys.readouterr().err
    assert main(["run", str(app), "--state", str(state), "--no-check", "--smoke", str(smoke)]) == 0


def test_run_returns_1_with_a_traceback_when_the_script_raises(project, tmp_path, capsys):
    app = write_app(project, APP.format(name="records"))
    smoke = tmp_path / "smoke.py"
    smoke.write_text("def main(app):\n    raise RuntimeError('boom')\n")

    assert main(["run", str(app), "--state", str(tmp_path / "s"), "--smoke", str(smoke)]) == 1
    err = capsys.readouterr().err
    assert "Traceback" in err and "boom" in err


def test_python_dash_m_entry_point_runs_check(project, tmp_path):
    clean = write_app(project, APP.format(name="records"))
    ok = subprocess.run(
        [sys.executable, "-m", "pyifc.cli", "check", str(clean)], capture_output=True, text=True, cwd=tmp_path
    )
    assert ok.returncode == 0 and ok.stderr == ""

    bad = write_app(project, UNDECLARED)
    failed = subprocess.run(
        [sys.executable, "-m", "pyifc.cli", "check", str(bad)], capture_output=True, text=True, cwd=tmp_path
    )
    assert failed.returncode == 1
    assert "app.py:" in failed.stderr and "error E_UNDECLARED_USE:" in failed.stderr


@pytest.mark.parametrize(
    "arguments",
    [
        ["check"],
        ["graph", "-o", "{tmp}/g.json"],
        ["generate", "--out", "{tmp}/out", "--init-lock"],
        ["run", "--state", "{tmp}/state", "--smoke", "{tmp}/smoke.py"],
        ["run", "--state", "{tmp}/state", "--no-check", "--smoke", "{tmp}/smoke.py"],
    ],
)
def test_missing_app_file_is_an_input_error_not_a_traceback(arguments, tmp_path, capsys):
    (tmp_path / "smoke.py").write_text("def main(app):\n    return 0\n")
    missing = tmp_path / "nope.py"
    command, *rest = [item.replace("{tmp}", str(tmp_path)) for item in arguments]

    assert main([command, str(missing), *rest]) == 1

    err = capsys.readouterr().err
    assert f"{missing}: error E_APP_UNREADABLE:" in err
    assert "Traceback" not in err
    assert not (tmp_path / "out" / "app.tf.json").exists()


def test_undecodable_app_file_is_an_input_error(project, capsys):
    app = project / "app.py"
    app.write_bytes(b"\xff\xfe\x00bad")

    assert main(["check", str(app)]) == 1
    assert "E_APP_UNREADABLE" in capsys.readouterr().err


NIGHTLY = textwrap.dedent('''
    from pyifc import Application, Schedule

    app = Application("tiny")
    nightly = Schedule("nightly", cron="0 3 * * 1")


    @app.scheduled(nightly, id="job")
    def job():
        pass
''')


def test_unsupported_by_target_exits_2_and_writes_only_diagnostics(project, tmp_path, capsys):
    app = write_app(project, NIGHTLY)
    out = tmp_path / "out"

    assert generate(app, out, "--init-lock") == 2

    assert "E_UNSUPPORTED_BY_TARGET" in capsys.readouterr().err
    assert not (out / "app.tf.json").exists()
    assert not (project / "pyifc.lock.json").exists()
    diagnostics = json.loads((out / "diagnostics.json").read_text())
    assert any(
        item["code"] == "E_UNSUPPORTED_BY_TARGET" and "numeric day-of-week" in item["message"]
        for item in diagnostics
    )


def test_unknown_config_key_exits_2(project, tmp_path, capsys):
    app = write_app(project, APP.format(name="records"))
    (project / "pyifc.toml").write_text('regoin = "eu-west-1"\n')

    assert generate(app, tmp_path / "out", "--init-lock") == 2

    err = capsys.readouterr().err
    assert "E_CONFIG" in err and "regoin" in err
    assert not (tmp_path / "out" / "app.tf.json").exists()


def test_non_string_config_value_exits_2(project, tmp_path, capsys):
    app = write_app(project, APP.format(name="records"))
    (project / "pyifc.toml").write_text("region = 5\n")

    assert generate(app, tmp_path / "out", "--init-lock") == 2

    assert "E_CONFIG" in capsys.readouterr().err


def test_corrupt_lock_leaves_an_existing_terraform_file_untouched(project, tmp_path):
    app = write_app(project, APP.format(name="records"))
    out = tmp_path / "out"
    assert generate(app, out, "--init-lock") == 0
    terraform = (out / "app.tf.json").read_bytes()
    (project / "pyifc.lock.json").write_text("{not json")

    assert generate(app, out) == 2

    assert (out / "app.tf.json").read_bytes() == terraform
    assert (project / "pyifc.lock.json").read_text() == "{not json"


UNUSED_LIST = textwrap.dedent('''
    from pyifc import Application, Bucket

    app = Application("tiny")
    records = Bucket("records")


    @app.handler(method="POST", path="/records", id="ingest")
    @app.write(records)
    @app.list(records)
    def ingest(body, content_type):
        records.put("k", body)
        return {}
''')


def test_declared_but_unused_capability_is_a_dropped_grant_in_used_mode(project, tmp_path, capsys):
    app = write_app(project, UNUSED_LIST)
    out = tmp_path / "out"

    assert generate(app, out, "--init-lock") == 0

    assert "W_GRANT_DROPPED" in capsys.readouterr().err
    diagnostics = json.loads((out / "diagnostics.json").read_text())
    dropped = [item for item in diagnostics if item["code"] == "W_GRANT_DROPPED"]
    assert len(dropped) == 1
    assert "list on 'records'" in dropped[0]["message"]
    assert dropped[0]["severity"] == "warning" and dropped[0]["file"] == "app.py"


def test_declared_mode_drops_no_grants(project, tmp_path):
    app = write_app(project, UNUSED_LIST)
    out = tmp_path / "out"

    assert generate(app, out, "--init-lock", "--iam-mode", "declared") == 0

    diagnostics = json.loads((out / "diagnostics.json").read_text())
    assert not any(item["code"] == "W_GRANT_DROPPED" for item in diagnostics)


def test_written_files_have_normal_permissions(project, tmp_path):
    app = write_app(project, APP.format(name="records"))
    out = tmp_path / "out"
    current = os.umask(0o022)
    try:
        assert generate(app, out, "--init-lock") == 0
        graph = tmp_path / "g.json"
        assert main(["graph", str(app), "-o", str(graph)]) == 0
    finally:
        os.umask(current)

    for path in (out / "app.tf.json", out / "diagnostics.json", graph):
        assert stat.S_IMODE(path.stat().st_mode) == 0o644, path
