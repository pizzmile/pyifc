import base64
import hashlib
import os
import zipfile

import pytest

from pyifc.backends.aws.package import PackageResult, build_package


def make_sources(root, payload=b"print('a')\n"):
    src = root / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "b.py").write_bytes(payload)
    (src / "pkg" / "a.py").write_bytes(b"x = 1\n")
    (src / "top.py").write_bytes(b"y = 2\n")
    (src / "pkg" / "__pycache__").mkdir()
    (src / "pkg" / "__pycache__" / "a.cpython-311.pyc").write_bytes(b"junk")
    return src


def test_same_sources_give_identical_bytes_and_hash(tmp_path):
    src = make_sources(tmp_path)

    first = build_package([src], tmp_path / "one.zip")
    second = build_package([src], tmp_path / "two.zip")

    assert isinstance(first, PackageResult)
    assert first.base64sha256 == second.base64sha256
    assert first.path.read_bytes() == second.path.read_bytes()


def test_hash_is_base64_sha256_of_the_zip_bytes(tmp_path):
    result = build_package([make_sources(tmp_path)], tmp_path / "out.zip")

    expected = base64.b64encode(hashlib.sha256(result.path.read_bytes()).digest()).decode()
    assert result.base64sha256 == expected
    assert result.path == tmp_path / "out.zip"


def test_changing_one_source_byte_changes_the_hash(tmp_path):
    one = build_package([make_sources(tmp_path / "one")], tmp_path / "one.zip")
    two = build_package([make_sources(tmp_path / "two", b"print('b')\n")], tmp_path / "two.zip")

    assert one.base64sha256 != two.base64sha256


def test_entries_are_sorted_with_fixed_timestamp_and_no_extra_attributes(tmp_path):
    result = build_package([make_sources(tmp_path)], tmp_path / "out.zip")

    with zipfile.ZipFile(result.path) as archive:
        infos = archive.infolist()
    assert [info.filename for info in infos] == ["pkg/a.py", "pkg/b.py", "top.py"]
    for info in infos:
        assert info.date_time == (2020, 1, 1, 0, 0, 0)
        assert info.extra == b""
        assert info.external_attr == 0o644 << 16
        assert info.create_system == 3


def test_several_source_dirs_are_merged(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a.py").write_text("a")
    (second / "b.py").write_text("b")

    result = build_package([first, second], tmp_path / "out.zip")

    with zipfile.ZipFile(result.path) as archive:
        assert archive.namelist() == ["a.py", "b.py"]


def test_missing_source_directory_is_reported_by_name(tmp_path):
    missing = tmp_path / "no-such-dir"

    with pytest.raises(FileNotFoundError, match="no-such-dir"):
        build_package([missing], tmp_path / "out.zip")
    assert not (tmp_path / "out.zip").exists()


def test_output_inside_a_source_directory_is_rejected(tmp_path):
    src = make_sources(tmp_path)

    with pytest.raises(ValueError, match="inside"):
        build_package([src], src / "pkg" / "out.zip")
    assert not (src / "pkg" / "out.zip").exists()


def test_file_modification_time_and_mode_do_not_change_the_package(tmp_path):
    src = make_sources(tmp_path)
    before = build_package([src], tmp_path / "one.zip")
    os.utime(src / "top.py", (1_000_000_000, 1_000_000_000))
    os.chmod(src / "top.py", 0o755)
    after = build_package([src], tmp_path / "two.zip")

    assert before.base64sha256 == after.base64sha256
