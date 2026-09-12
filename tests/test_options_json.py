"""Unit tests for the options.json helpers in conftest.py.

`_parse_options_file` validates a single file's schema;
`load_case_options` walks up from the case dir merging every
options.json on the way; `_pick_main_src` picks the entry-point file
based on plugin-claimed extensions. None of these is exercised
directly by the parametrized test_case suite -- per-test regressions
land here.
"""

from pathlib import Path

import pytest

import conftest


# --- _parse_options_file --------------------------------------------------

def test_parse_options_file_missing_returns_empty(tmp_path: Path) -> None:
    cfg = conftest._parse_options_file(tmp_path / "nope.json")
    assert cfg == {}


def test_parse_options_file_minimal_default_int(tmp_path: Path) -> None:
    p = tmp_path / "options.json"
    p.write_text('{"default_int": "int64"}')
    assert conftest._parse_options_file(p) == {"default_int": "int64"}


def test_parse_options_file_plugin_and_dsl_opts(tmp_path: Path) -> None:
    p = tmp_path / "options.json"
    p.write_text(
        '{"plugin": "frontends/x/x.py", '
        '"dsl_opts": {"sdl": "off", "trace": "1"}}'
    )
    cfg = conftest._parse_options_file(p)
    assert cfg["plugin"] == "frontends/x/x.py"
    assert cfg["dsl_opts"] == {"sdl": "off", "trace": "1"}


def test_parse_options_file_rejects_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "options.json"
    p.write_text("{not valid json")
    with pytest.raises(BaseException, match="invalid JSON"):
        conftest._parse_options_file(p)


def test_parse_options_file_rejects_non_object_root(tmp_path: Path) -> None:
    p = tmp_path / "options.json"
    p.write_text("[1, 2, 3]")
    with pytest.raises(BaseException, match="expected JSON object"):
        conftest._parse_options_file(p)


def test_parse_options_file_rejects_unknown_key(tmp_path: Path) -> None:
    p = tmp_path / "options.json"
    p.write_text('{"weird_key": "x"}')
    with pytest.raises(BaseException, match="unsupported keys"):
        conftest._parse_options_file(p)


def test_parse_options_file_rejects_non_string_plugin(tmp_path: Path) -> None:
    p = tmp_path / "options.json"
    p.write_text('{"plugin": 42}')
    with pytest.raises(BaseException, match="'plugin' must be a string"):
        conftest._parse_options_file(p)


def test_parse_options_file_rejects_non_dict_dsl_opts(tmp_path: Path) -> None:
    p = tmp_path / "options.json"
    p.write_text('{"dsl_opts": "nope"}')
    with pytest.raises(BaseException, match="'dsl_opts' must be a dict"):
        conftest._parse_options_file(p)


def test_parse_options_file_rejects_non_string_dsl_opt_value(
    tmp_path: Path,
) -> None:
    p = tmp_path / "options.json"
    p.write_text('{"dsl_opts": {"sdl": 1}}')
    with pytest.raises(BaseException, match="dsl_opts 'sdl' must be a string"):
        conftest._parse_options_file(p)


def test_parse_options_file_rejects_bad_default_int(tmp_path: Path) -> None:
    p = tmp_path / "options.json"
    p.write_text('{"default_int": "nope"}')
    with pytest.raises(BaseException):
        conftest._parse_options_file(p)


# --- load_case_options (walk-up merge) ------------------------------------

def _layered_case_dir(tmp_path: Path, group_cfg: str | None,
                      case_cfg: str | None) -> Path:
    """Build a fake `tests/cases/<group>/<case>/` layout with optional
    group-level and case-level options.json contents and monkey-patch
    `conftest.CASES_DIR` to point at the synthetic root. Returns the
    case directory."""
    cases_root = tmp_path / "cases"
    group_dir = cases_root / "group"
    case_dir = group_dir / "case"
    case_dir.mkdir(parents=True)
    if group_cfg is not None:
        (group_dir / "options.json").write_text(group_cfg)
    if case_cfg is not None:
        (case_dir / "options.json").write_text(case_cfg)
    return case_dir


def test_load_case_options_walks_up_to_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_dir = _layered_case_dir(
        tmp_path,
        group_cfg='{"plugin": "frontends/x/x.py", "dsl_opts": {"a": "1"}}',
        case_cfg=None,
    )
    monkeypatch.setattr(conftest, "CASES_DIR", tmp_path / "cases")
    cfg = conftest.load_case_options(case_dir)
    assert cfg["plugin"] == "frontends/x/x.py"
    assert cfg["dsl_opts"] == {"a": "1"}


def test_load_case_options_per_case_overrides_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_dir = _layered_case_dir(
        tmp_path,
        group_cfg='{"plugin": "frontends/x/x.py", "default_int": "int32"}',
        case_cfg='{"default_int": "int64"}',
    )
    monkeypatch.setattr(conftest, "CASES_DIR", tmp_path / "cases")
    cfg = conftest.load_case_options(case_dir)
    # Group-level plugin survives, per-case default_int wins.
    assert cfg["plugin"] == "frontends/x/x.py"
    assert cfg["default_int"] == "int64"


def test_load_case_options_dsl_opts_merge_keywise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_dir = _layered_case_dir(
        tmp_path,
        group_cfg=(
            '{"plugin": "frontends/x/x.py", '
            '"dsl_opts": {"sdl": "off", "trace": "0"}}'
        ),
        case_cfg='{"dsl_opts": {"trace": "1"}}',
    )
    monkeypatch.setattr(conftest, "CASES_DIR", tmp_path / "cases")
    cfg = conftest.load_case_options(case_dir)
    # The group's "sdl" key survives, the case's "trace" overrides --
    # confirms the merge direction is group-first then case-on-top.
    assert cfg["dsl_opts"] == {"sdl": "off", "trace": "1"}


def test_parse_options_file_snapshot_lib_modules(tmp_path: Path) -> None:
    p = tmp_path / "options.json"
    p.write_text('{"snapshot_lib_modules": ["itertools", "tplib.box"]}')
    cfg = conftest._parse_options_file(p)
    assert cfg["snapshot_lib_modules"] == ["itertools", "tplib.box"]


def test_parse_options_file_rejects_non_list_snapshot_lib_modules(
    tmp_path: Path,
) -> None:
    p = tmp_path / "options.json"
    p.write_text('{"snapshot_lib_modules": "itertools"}')
    with pytest.raises(BaseException, match="snapshot_lib_modules"):
        conftest._parse_options_file(p)


def test_parse_options_file_rejects_non_string_snapshot_lib_module(
    tmp_path: Path,
) -> None:
    p = tmp_path / "options.json"
    p.write_text('{"snapshot_lib_modules": ["ok", 7]}')
    with pytest.raises(BaseException, match="snapshot_lib_modules"):
        conftest._parse_options_file(p)


def test_get_case_snapshot_lib_modules_defaults_to_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_dir = _layered_case_dir(tmp_path, group_cfg=None, case_cfg=None)
    monkeypatch.setattr(conftest, "CASES_DIR", tmp_path / "cases")
    assert conftest.get_case_snapshot_lib_modules(case_dir) == frozenset()


def test_snapshot_lib_modules_replaces_rather_than_unions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A list key follows the plain override rule -- only dsl_opts merges --
    so the case's list is exactly what gets snapshotted."""
    case_dir = _layered_case_dir(
        tmp_path,
        group_cfg='{"snapshot_lib_modules": ["itertools"]}',
        case_cfg='{"snapshot_lib_modules": ["heapq"]}',
    )
    monkeypatch.setattr(conftest, "CASES_DIR", tmp_path / "cases")
    assert conftest.get_case_snapshot_lib_modules(case_dir) == {"heapq"}


# --- snapshot_lib_modules: pattern matching + the zero-match guard ---------

def test_snapshot_lib_pattern_hits_exact_and_glob() -> None:
    """An exact name is a glob with no metacharacter, so both forms take the
    same path; `*` spans dots, which is what makes `["*"]` mean the library."""
    pats = frozenset({"heapq", "os.*", "*"})
    assert conftest.snapshot_lib_pattern_hits("heapq", pats) == {"heapq", "*"}
    assert conftest.snapshot_lib_pattern_hits("os.path", pats) == {"os.*", "*"}
    assert conftest.snapshot_lib_pattern_hits("tplib.json.parser",
                                              frozenset({"*"})) == {"*"}
    assert conftest.snapshot_lib_pattern_hits("heapq",
                                              frozenset({"Heapq"})) == set()


def _compile_trivial(tmp_path: Path, patterns: set[str]):
    src = tmp_path / "main.py"
    src.write_text("def main() -> None:\n    print(1)\n\n\nmain()\n")
    return conftest.compile_with_diagnostics(
        src, tmp_path / "out", snapshot_lib_modules=frozenset(patterns))


def test_snapshot_lib_modules_exact_name_matching_nothing_fails(
    tmp_path: Path,
) -> None:
    """A renamed module must break the case rather than quietly narrow what it
    snapshots -- the guard this pins was previously unexercised."""
    with pytest.raises(BaseException, match="matched no library module"):
        _compile_trivial(tmp_path, {"no_such_module"})


def test_snapshot_lib_modules_glob_matching_nothing_fails(
    tmp_path: Path,
) -> None:
    with pytest.raises(BaseException, match="matched no library module"):
        _compile_trivial(tmp_path, {"no_such_package.*"})


def test_snapshot_lib_modules_glob_resolves_to_compiled_modules(
    tmp_path: Path,
) -> None:
    result = _compile_trivial(tmp_path, {"tpy.*"})
    assert result.success, result.diagnostics
    assert result.snapshot_lib_modules
    assert all(n.startswith("tpy.") for n in result.snapshot_lib_modules)


def test_snapshot_lib_modules_star_takes_every_library_module(
    tmp_path: Path,
) -> None:
    result = _compile_trivial(tmp_path, {"*"})
    assert result.success, result.diagnostics
    assert "builtins" in result.snapshot_lib_modules


def test_snapshot_lib_modules_one_dead_pattern_among_live_ones_fails(
    tmp_path: Path,
) -> None:
    """Per-ENTRY, not per-set: a live `*` must not cover for a dead sibling."""
    with pytest.raises(BaseException, match="no_such_module"):
        _compile_trivial(tmp_path, {"*", "no_such_module"})


def test_load_case_options_no_files_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_dir = _layered_case_dir(tmp_path, group_cfg=None, case_cfg=None)
    monkeypatch.setattr(conftest, "CASES_DIR", tmp_path / "cases")
    assert conftest.load_case_options(case_dir) == {}


# --- _pick_main_src --------------------------------------------------------

def _f(name: str) -> Path:
    """Pseudo-Path that pretends to live under /src/. The function
    only reads .suffix / .stem / .name, so plain Path objects work
    without touching the filesystem."""
    return Path("/src") / name


def test_pick_main_src_single_py() -> None:
    assert conftest._pick_main_src([_f("main.py")], []) == _f("main.py")


def test_pick_main_src_plugin_ext_wins_over_py() -> None:
    # When a plugin claims `.pas`, the .pas file is preferred even
    # if a sibling `main.py` exists -- the `.py` is treated as a
    # helper, not the entry point.
    got = conftest._pick_main_src(
        [_f("main.py"), _f("main.pas"), _f("helper.py")], [".pas"])
    assert got == _f("main.pas")


def test_pick_main_src_prefers_main_stem_within_plugin_files() -> None:
    got = conftest._pick_main_src(
        [_f("aux.pas"), _f("main.pas"), _f("zzz.pas")], [".pas"])
    assert got == _f("main.pas")


def test_pick_main_src_alphabetical_fallback_when_no_main(
) -> None:
    got = conftest._pick_main_src(
        [_f("zzz.pas"), _f("alpha.pas"), _f("beta.pas")], [".pas"])
    assert got == _f("alpha.pas")


def test_pick_main_src_falls_back_to_py_when_no_plugin_files() -> None:
    got = conftest._pick_main_src(
        [_f("main.py"), _f("helper.py")], [".pas"])
    assert got == _f("main.py")


def test_pick_main_src_ignores_irrelevant_extensions() -> None:
    # `src_files` only ever contains files matched by the entry-ext
    # glob, but the picker shouldn't break if some other file slips
    # in -- candidates fall back through the (plugin -> py -> any)
    # chain and still return something.
    got = conftest._pick_main_src(
        [_f("README.md"), _f("main.py")], [".pas"])
    assert got == _f("main.py")
