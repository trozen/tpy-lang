"""Regression tests for the THIR per-case migration harness helpers."""

import ast
import contextlib
import copy
from pathlib import Path

import pytest

import conftest


def test_apply_no_thir_marker(tmp_path: Path) -> None:
    """--thir-classify's marker writer (`_apply_no_thir_marker`): add a
    no_thir.txt for a dirty (has-fallback) case, remove it for a clean one,
    idempotent both ways -- a bug here would mis-mark/unmark cases repo-wide the
    next time --thir-classify runs."""
    marker = tmp_path / "no_thir.txt"

    conftest._apply_no_thir_marker(tmp_path, dirty=True)
    assert marker.exists()

    conftest._apply_no_thir_marker(tmp_path, dirty=True)  # idempotent add
    assert marker.exists()

    conftest._apply_no_thir_marker(tmp_path, dirty=False)
    assert not marker.exists()

    conftest._apply_no_thir_marker(tmp_path, dirty=False)  # idempotent remove
    assert not marker.exists()


class _StubConfig:
    """Minimal config.getoption stand-in: unset flags read False."""

    def __init__(self, opts: dict) -> None:
        self._opts = opts

    def getoption(self, name: str):
        return self._opts.get(name, False)


def test_thir_flag_conflict() -> None:
    """The mutually-exclusive THIR flag guard: --no-thir (disable) can't pair
    with the force-on flags nor with --update-snapshots (THIR authors the
    snapshots), and the two marker-managing flags can't pair with
    --update-snapshots either. Anything else is fine."""
    conflict = conftest._thir_flag_conflict

    # --no-thir vs each force-on flag -> conflict.
    for forcing in ("--thir-codegen", "--thir-classify", "--thir-check-flip"):
        assert conflict(_StubConfig({"--no-thir": True, forcing: True}),
                        updating=False) is not None

    # Regenerating through the AST would commit the wrong author's output.
    assert conflict(_StubConfig({"--no-thir": True}), updating=True) is not None

    # The marker writers/readers have no business in a regeneration run.
    for marker_flag in ("--thir-classify", "--thir-check-flip"):
        assert conflict(_StubConfig({marker_flag: True}),
                        updating=True) is not None

    # --thir-stdlib needs THIR ACTIVE, so it conflicts with the one way of
    # turning it off. It is not a force-on flag (it respects no_thir markers),
    # hence its own branch rather than membership in the loop above.
    assert conflict(_StubConfig({"--thir-stdlib": True, "--no-thir": True}),
                    updating=False) is not None

    # --thir-strict needs THIR active too: with the overlay off there is no
    # fallback to refuse, so the flag would be silently inert.
    assert conflict(_StubConfig({"--thir-strict": True}), updating=True) is not None
    assert conflict(_StubConfig({"--thir-strict": True, "--no-thir": True}),
                    updating=False) is not None
    assert conflict(_StubConfig({"--thir-strict": True}), updating=False) is None

    # ...and asking for it on and off at once.
    assert conflict(_StubConfig({"--thir-stdlib": True,
                                 "--no-thir-stdlib": True}),
                    updating=False) is not None

    # Non-conflicting combinations -> None.
    assert conflict(_StubConfig({}), updating=False) is None          # default
    assert conflict(_StubConfig({"--no-thir": True}), updating=False) is None
    assert conflict(_StubConfig({"--thir-codegen": True}), updating=False) is None
    assert conflict(_StubConfig({"--thir-stdlib": True}), updating=False) is None
    assert conflict(_StubConfig({"--thir-stdlib": True, "--thir-codegen": True}),
                    updating=False) is None
    # Regeneration is an ordinary THIR run: the oracle and the marker-ignoring
    # flag both ride along rather than aborting it.
    assert conflict(_StubConfig({"--thir-stdlib": True}), updating=True) is None
    assert conflict(_StubConfig({"--thir-codegen": True}), updating=True) is None
    assert conflict(_StubConfig({"--no-thir-stdlib": True}), updating=True) is None
    assert conflict(_StubConfig({}), updating=True) is None


def test_thir_stdlib_default_on() -> None:
    """The stdlib oracle is ON unless something turns it off. An opt-in oracle
    is only as good as the flag people remember to pass, and stdlib emission at
    a case's own instantiations and options has no committed snapshot to fall
    back on -- so the DEFAULT carries the check and every way off is
    explicit."""
    on = conftest._thir_stdlib_enabled

    assert on(_StubConfig({})) is True
    assert on(_StubConfig({"--thir-codegen": True})) is True
    # The explicit flag stays a no-op, not a second switch.
    assert on(_StubConfig({"--thir-stdlib": True})) is True

    # The opt-out, and the one state with nothing to diff against.
    assert on(_StubConfig({"--no-thir-stdlib": True})) is False
    assert on(_StubConfig({"--no-thir": True})) is False


def _mode(**kw):
    base = dict(thir_codegen=True, no_thir=False, ignore_markers=False,
                classify=False, check_flip=False)
    return conftest._thir_case_mode(**{**base, **kw})


def test_thir_case_mode_overlay_runs_for_marked_cases() -> None:
    """The overlay decision is INDEPENDENT of no_thir.txt. The marker is
    per-case but fallback is per-body, so a marked case still routes bodies that
    must be byte-diffed -- a fallback emits byte-identical AST, so nothing else
    would catch them regressing to AST."""
    assert _mode(no_thir=False)[0] is True
    assert _mode(no_thir=True)[0] is True
    # ...and the ratchet is the half that DOES respect the marker.
    assert _mode(no_thir=False)[1] is True
    assert _mode(no_thir=True)[1] is False


def test_thir_case_mode_off_disables_both() -> None:
    """--no-thir (thir_codegen=False): no oracle pass, no ratchet, marked or
    not."""
    for marked in (False, True):
        assert _mode(thir_codegen=False, no_thir=marked) == (False, False)


def test_thir_case_mode_whole_corpus_and_writers_suppress_ratchet() -> None:
    """--thir-codegen / -classify / -check-flip keep the overlay but drop the
    ratchet: they consume the raw fallback count (to measure, mark, or list
    flip candidates), so an unmarked case falling back must not fail comp."""
    assert _mode(ignore_markers=True) == (True, False)
    assert _mode(ignore_markers=True, classify=True) == (True, False)
    assert _mode(ignore_markers=True, check_flip=True) == (True, False)
    # Guard the flags independently of ignore_markers, which they happen to
    # imply today -- the ratchet must not resurrect if that coupling changes.
    assert _mode(classify=True) == (True, False)
    assert _mode(check_flip=True) == (True, False)


_TALLIES = ("_thir_tally", "_thir_cases", "_thir_faces", "_thir_fallback",
            "_thir_arm_residual", "_thir_shapes", "_thir_flip",
            "_interop_thir")


@contextlib.contextmanager
def _isolated_tallies():
    """Compiling a fixture case feeds the run-wide THIR tallies. Restore them,
    or these tests would inflate the very dial + coverage metrics they guard."""
    saved = {n: copy.deepcopy(getattr(conftest, n)) for n in _TALLIES}
    try:
        yield
    finally:
        for name, value in saved.items():
            getattr(conftest, name).clear()
            if isinstance(value, list):
                getattr(conftest, name).extend(value)
            else:
                getattr(conftest, name).update(value)


def _compile_fixture_case(tmp_path: Path, marked: bool):
    case = tmp_path / "case"
    (case / "src").mkdir(parents=True)
    (case / "src" / "main.py").write_text(
        "def f(x: int) -> int:\n    return x + 1\n\n\nprint(f(1))\n")
    if marked:
        (case / "no_thir.txt").write_text("marked\n")
    with _isolated_tallies():
        return conftest.compile_with_diagnostics(
            case / "src" / "main.py", tmp_path / "out")


def _require_thir():
    if not conftest.TEST_CODEGEN_OPTIONS.thir_codegen:
        pytest.skip("THIR off (--no-thir)")


def test_marked_case_still_gets_an_overlay(tmp_path: Path) -> None:
    """The integration half of `_thir_case_mode`: a no_thir-marked case must
    STILL re-emit its user modules through the AST oracle, because the marker
    is per-case while fallback is per-body.

    Nothing else can catch a regression here. Re-gating the oracle pass on the
    marker leaves the whole corpus green -- a marked case byte-diffs identically
    whether or not that pass ran, since it only ADDS a check. So a green suite
    is not evidence; this assertion is."""
    _require_thir()
    result = _compile_fixture_case(tmp_path, marked=True)
    assert result.success, result.diagnostics
    assert result.ast_modules, "marked case got no AST oracle pass"
    # Depends on the fixture body staying routable; if THIR ever stops routing
    # `return x + 1`, this fires on the routing, not on the overlay.
    assert result.thir_routed_names, "marked case recorded no routed bodies"
    if not conftest.THIR_IGNORE_MARKERS:
        assert result.thir_ratchet_fell is None, "ratchet must skip a marked case"


def test_unmarked_case_arms_the_ratchet(tmp_path: Path) -> None:
    """The complement: an unmarked case is governed by the ratchet, so the
    harness must surface its fallback count (0 for this clean body) rather than
    None. Pins that the ratchet stays wired to the marker after the split."""
    _require_thir()
    if (conftest.THIR_IGNORE_MARKERS or conftest.THIR_CLASSIFY_WRITE
            or conftest.THIR_CHECK_FLIP):
        pytest.skip("ratchet suppressed by the marker-ignoring flags")
    result = _compile_fixture_case(tmp_path, marked=False)
    assert result.success, result.diagnostics
    assert result.ast_modules, "unmarked case got no AST oracle pass"
    assert result.thir_ratchet_fell == 0, "ratchet not armed for an unmarked case"


def test_thir_stdlib_wiring_reaches_the_compile(request: pytest.FixtureRequest,
                                                monkeypatch,
                                                tmp_path: Path) -> None:
    """The default-on decision has to survive two hops that nothing else pins:
    pytest_configure must publish `_thir_stdlib_enabled`'s verdict as the
    module-level THIR_STDLIB, and compile_with_diagnostics must turn that flag
    into the both-paths module list test_case byte-compares.

    Either hop breaking just stops the stdlib oracle running -- the corpus stays
    green, because the oracle only ADDS a comparison. So a green suite is not
    evidence; these assertions are. The flag is monkeypatched both ways so the
    list is pinned to THIS switch, not merely to being non-empty."""
    _require_thir()
    assert conftest.THIR_STDLIB is conftest._thir_stdlib_enabled(
        request.config), (
        "pytest_configure did not publish the resolver's verdict as THIR_STDLIB")

    monkeypatch.setattr(conftest, "THIR_STDLIB", True)
    on = _compile_fixture_case(tmp_path / "on", marked=False)
    assert on.success, on.diagnostics
    assert on.thir_lib_modules, (
        "THIR_STDLIB is on but the compile handed test_case no stdlib module "
        "pair -- the oracle compares nothing")
    # ...and the pairs must actually be readable: test_case reads both sides of
    # every non-None pair, so a list of unemitted paths would be an oracle that
    # errors rather than compares. Declaration-only (`native_module`) modules
    # emit nothing and carry no pair, which is why this counts rather than
    # requiring one per module.
    pairs = 0
    for mod_name, ast_hpp, ast_cpp, thir_hpp, thir_cpp in on.thir_lib_modules:
        for ast_path, thir_path in ((ast_hpp, thir_hpp), (ast_cpp, thir_cpp)):
            if ast_path is None or thir_path is None:
                continue
            pairs += 1
            assert ast_path.exists() and thir_path.exists(), (
                f"{mod_name}: {ast_path} / {thir_path} not emitted")
    assert pairs, "the stdlib oracle got module entries but no comparable file"

    monkeypatch.setattr(conftest, "THIR_STDLIB", False)
    off = _compile_fixture_case(tmp_path / "off", marked=False)
    assert off.success, off.diagnostics
    assert off.thir_lib_modules is None, (
        "--no-thir-stdlib must leave the stdlib oracle off entirely")


# --- the codegen-error gate ----------------------------------------------


class _StubCodegenModule:
    """The two attributes `_assert_both_paths_reject` reads off a module."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.name = "main"


class _StubCompiler:
    """A `generate_code` whose re-emit does whatever the caller needs, so the
    gate's own branches can be reached without a source that provokes them."""

    def __init__(self, re_emit) -> None:
        self._re_emit = re_emit
        self.emitted: list[str] = []

    def generate_code(self, mod, out_dir, *, entry_module_name, options):
        self.emitted.append(mod.name)
        self._re_emit()
        return None, None


def _run_error_gate(tmp_path: Path, re_emit, case_dir: Path | None = None,
                    thir_err=None):
    """Drive `_assert_both_paths_reject` over a stub whose AST re-emit is
    `re_emit`. Returns the stub, so a caller can prove the re-emit ran."""
    src = tmp_path / "src" / "main.py"
    src.parent.mkdir(parents=True)
    src.write_text("x = 1\n")
    entry = _StubCodegenModule(src)
    stub = _StubCompiler(re_emit)
    conftest._assert_both_paths_reject(
        stub, [entry], entry, tmp_path / "out", src,
        case_dir if case_dir is not None else tmp_path,
        thir_err if thir_err is not None
        else conftest.CodeGenError("cannot lower this"))
    return stub


def test_error_gate_fires_when_the_ast_emits_where_thir_raised(
        tmp_path: Path) -> None:
    """The gate's whole point is its FAILING branch, and with both known
    offenders fixed the corpus only ever takes its passing one. Deleting the
    gate outright would leave the suite green, so a green suite is not evidence
    that it still catches anything; this assertion is.

    Constructed at the gate rather than from source: the shape it must catch is
    a body THIR rejects and the AST emits, and no such source is supposed to
    exist in the tree."""
    with pytest.raises(pytest.fail.Exception) as excinfo:
        _run_error_gate(tmp_path, lambda: None)
    assert "the AST path emitted code where THIR raised" in str(excinfo.value)


def test_error_gate_fires_on_a_different_diagnostic(tmp_path: Path) -> None:
    """The other failing branch: the AST rejecting for its OWN reason is not
    the same as agreeing with the diagnostic the case's `diag.txt` records."""
    def other():
        raise conftest.CodeGenError("some unrelated reject")

    with pytest.raises(pytest.fail.Exception) as excinfo:
        _run_error_gate(tmp_path, other)
    assert "DIFFERENT codegen diagnostic" in str(excinfo.value)


def test_error_gate_passes_on_the_same_diagnostic(tmp_path: Path) -> None:
    """The complement: the AST re-emit raises the same diagnostic, so the gate
    is silent. Asserting the re-emit RAN is what keeps this from passing
    vacuously."""
    def same():
        raise conftest.CodeGenError("cannot lower this")

    stub = _run_error_gate(tmp_path, same)
    assert stub.emitted == ["main"], "the gate never re-emitted"


def _raise_from(module_name: str, func_name: str, msg: str = "boom"):
    """Raise `CodeGenError` from a frame that claims `module_name` and
    `func_name`, so the author classification can be driven over every layer
    without importing the real emitters."""
    src = (f"def {func_name}(exc):\n"
           f"    raise exc\n")
    globs = {"__name__": module_name}
    exec(compile(src, "<probe>", "exec"), globs)
    try:
        globs[func_name](conftest.CodeGenError(msg))
    except conftest.CodeGenError as err:
        return err
    raise AssertionError("probe did not raise")


@pytest.mark.parametrize("module_name, func_name, want", [
    ("tpyc.codegen_cpp.statements", "_gen_simple_stmt", "body"),
    ("tpyc.codegen_cpp.match", "_emit_binding", "body"),
    # The row a file-based reading gets wrong: a SURVIVING module whose
    # callers all die.
    ("tpyc.codegen_cpp.context", "use_rebind_slot", "body"),
    ("tpyc.codegen_cpp.records", "_reject_nondef_ctor_field_in_body", "body"),
    # Skeleton diagnostics outlive the cutover; calling them blockers would
    # manufacture work that does not exist.
    ("tpyc.codegen_cpp.gen_async", "gen_coro_struct", "skeleton"),
    ("tpyc.codegen_cpp.generator", "_emit_resumable_structs", "skeleton"),
    ("tpyc.thir.lower.statements", "_lower_return", "thir"),
    # The name alone must not carry the verdict: the same function name in a
    # module that is not its home is a different function.
    ("tpyc.thir.emit", "use_rebind_slot", "skeleton"),
])
def test_diagnostic_author_classifies_by_caller(module_name, func_name, want):
    err = _raise_from(module_name, func_name)
    assert conftest._diagnostic_author(err) == want


def test_body_diagnostic_functions_all_exist() -> None:
    """The classification is keyed on function names, so a rename silently
    reclassifies a diagnostic and the ratchet then fails pointing the wrong
    way. Fail here instead, naming the function that moved.

    This asserts only that each name still EXISTS. It does not re-derive the
    claim that earned the name its place -- that every caller lives in a
    module the cutover deletes. Give one of these a surviving caller and the
    entry silently becomes wrong while this still passes. The static site
    inventory catches the case where the new caller is in THIR; a new
    SKELETON caller is caught by neither, so the claim is re-derived by
    reading, not by a gate."""
    repo = Path(__file__).resolve().parent.parent
    missing = []
    for module_name, func_name in sorted(conftest.BODY_DIAGNOSTIC_FUNCTIONS):
        path = repo / (module_name.replace(".", "/") + ".py")
        if not path.exists():
            missing.append(f"{module_name} (module gone)")
            continue
        tree = ast.parse(path.read_text())
        defined = {n.name for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if func_name not in defined:
            missing.append(f"{module_name}.{func_name}")
    assert not missing, (
        f"BODY_DIAGNOSTIC_FUNCTIONS names functions that no longer exist: "
        f"{missing}. Update it in the same change that moved them.")


def test_error_gate_fires_on_an_unrecorded_body_diagnostic(
        tmp_path: Path) -> None:
    """A diagnostic the cutover would delete, not recorded as such. The
    emitting path raises it from a body emitter because the body FELL BACK, so
    without this branch the matching text reads exactly like a re-homed
    diagnostic."""
    def same():
        raise conftest.CodeGenError("cannot lower this")

    from_body = _raise_from("tpyc.codegen_cpp.statements", "_gen_simple_stmt",
                            "cannot lower this")
    with pytest.raises(pytest.fail.Exception) as excinfo:
        _run_error_gate(tmp_path, same, thir_err=from_body)
    assert "authored by an AST body emitter" in str(excinfo.value)


def test_error_gate_fires_when_a_recorded_case_starts_passing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The other direction, which is what makes the record a ratchet rather
    than a suppression list: progress must force the entry out.

    The recorded set is synthesized rather than sampled: it is empty whenever
    every diagnostic is authored by a surviving layer, which is the state the
    project is trying to reach and hold, so borrowing a real entry makes this
    assertion disappear exactly when the gate matters most."""
    recorded = "harness/error_synthetic_recorded_case"
    monkeypatch.setattr(conftest, "AST_ONLY_DIAGNOSTICS",
                        frozenset({recorded}))
    case_dir = tmp_path / Path(recorded).parent.name / Path(recorded).name

    def same():
        raise conftest.CodeGenError("cannot lower this")

    with pytest.raises(pytest.fail.Exception) as excinfo:
        _run_error_gate(tmp_path, same, case_dir=case_dir)
    assert "remove it from AST_ONLY_DIAGNOSTICS" in str(excinfo.value)


# --- tests/interop ext-exec overlay --------------------------------------

_INTEROP_FIXTURE = (
    "# tpy: ext_module\n"
    "# leading comment block, then a docstring -- the shape whose AST/THIR\n"
    "# divergence is invisible without source comments.\n"
    '"""Fixture module."""\n'
    "from tpy import Int64\n"
    "from tpy.extern import export\n"
    "\n"
    "\n"
    "@export\n"
    "def twice(x: Int64) -> Int64:\n"
    "    return x * 2\n"
)


def _run_interop_overlay(tmp_path: Path, marked: bool):
    case = tmp_path / "fixture"
    (case / "src").mkdir(parents=True)
    mod_py = case / "src" / "fixture.py"
    mod_py.write_text(_INTEROP_FIXTURE)
    if marked:
        (case / "no_thir.txt").write_text("marked\n")
    out = tmp_path / "overlay"
    with _isolated_tallies():
        return conftest.run_interop_thir_overlay(mod_py, case, out), out


def test_interop_overlay_emits_with_source_comments(tmp_path: Path) -> None:
    """The overlay's whole reason for emitting BOTH sides itself: the ext-exec
    snapshots come from the tpyc CLI at emit_source_comments=False, so a
    THIR-vs-snapshot diff cannot see a source-comment divergence.

    Nothing else catches a regression here. Re-pointing the overlay at
    expected/ (or dropping comments) leaves the whole corpus green -- the
    comment class is exactly the part that would stop being checked. So a green
    suite is not evidence; this assertion is.
    """
    _require_thir()
    result, out = _run_interop_overlay(tmp_path, marked=False)
    assert result is not None, "overlay did not run with THIR on"
    assert not result.divergences, "\n\n".join(result.divergences)
    emitted = (out / "ast").rglob("fixture.cpp")
    text = next(emitted).read_text()
    assert "// # leading comment block" in text, (
        "the overlay emitted without source comments -- it is then blind to "
        "the comment-trivia divergence class it exists to catch")


def test_interop_marked_case_skips_only_the_ratchet(tmp_path: Path) -> None:
    """A marked interop case still byte-diffs (the marker is per-case, fallback
    is per-body), but the ratchet must not govern it.

    Asserting `is None` rather than `== 0` is what makes this non-vacuous: the
    fixture routes cleanly, so a count of 0 is what an UNGATED ratchet would
    report too, and the test could not tell the marker had been honoured.
    """
    _require_thir()
    if conftest.THIR_CLASSIFY_WRITE or conftest.THIR_CHECK_FLIP:
        pytest.skip("marker writers repurpose the overlay's return")
    result, _out = _run_interop_overlay(tmp_path, marked=True)
    assert result is not None, "marked case got no overlay"
    assert not result.divergences, "\n\n".join(result.divergences)
    if not conftest.THIR_IGNORE_MARKERS:
        assert result.ratchet_fell is None, "ratchet must skip a marked case"


def test_interop_unmarked_case_arms_the_ratchet(tmp_path: Path) -> None:
    """The complement: an unmarked case IS governed, so it reports a count (0
    for this clean fixture) rather than None."""
    _require_thir()
    if (conftest.THIR_IGNORE_MARKERS or conftest.THIR_CLASSIFY_WRITE
            or conftest.THIR_CHECK_FLIP):
        pytest.skip("ratchet suppressed by the marker-ignoring flags")
    result, _out = _run_interop_overlay(tmp_path, marked=False)
    assert result is not None, "unmarked case got no overlay"
    assert result.ratchet_fell == 0, "ratchet not armed for an unmarked case"
