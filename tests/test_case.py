"""Unified test case runner: compile + optional exec + optional CPython verification.

Each folder under tests/cases/ becomes one test item. The test always runs the
COMP phase (compile, snapshot check, annotation validation).

The EXEC phase skips when this exact build already ran green on this machine's
toolchain, tracked by a local content-addressed cache (see conftest's
exec-results helpers). This is local, not committed: a fresh checkout
re-verifies the C++ build+run once, then caches, so `--force-exec` is no longer
needed to exercise exec. The CPY phase skips via committed fingerprints
(CPython output is toolchain-independent, so a committed key is portable).

Flags:
    --cxx VALUE          C++ toolchain for exec (mirrors `tpyc --cxx`; `list`
                         to enumerate). Re-keys the stdlib/PCH/exec caches.
    --force-exec         Run exec/cpy phases unconditionally (bypass auto-skip).
    --clean              Wipe shared PCH + stdlib .o + exec-results caches;
                         implies --force-exec.
    --update-snapshots   Regenerate expected files + fingerprints; implies
                         --force-exec. Equivalent to UPDATE_EXPECTED=1.
    UPDATE_EXPECTED=1    Env-var form of --update-snapshots (for CI / wrappers).
    TPY_KEEP_TEST_BINARIES=1  Keep linked per-case binaries after a passing
                         exec phase (default: deleted -- nothing reads them
                         and they accumulate ~2.3MB per case).
"""

import shutil
import warnings
from pathlib import Path

import pytest

from conftest import (
    UPDATE_EXPECTED,
    KEEP_TEST_BINARIES,
    case_binary_path,
    get_module_name,
    plugin_extensions_for,
    compile_with_diagnostics,
    get_case_default_int,
    get_case_snapshot_lib_modules,
    validate_annotations,
    validate_type_annotations,
    validate_non_null_annotations,
    validate_bounds_annotations,
    validate_div_annotations,
    validate_cast_annotations,
    validate_send_sync_annotations,
    validate_frame_annotations,
    validate_mir_annotations,
    validate_mir_fact_annotations,
    error_case_annotation_problems,
    fail_annotations,
    check_or_update,
    discover_cases,
    module_to_expected_path,
    find_extra_src_files,
    find_extra_include_dirs,
    find_force_includes,
    build_and_run,
    cpy_phase_applicable,
    exec_is_cross,
    get_stdlib_cache,
    merge_link_flags,
    plan_exec_phase,
    run_cpython,
    compute_session_fingerprints,
    read_session_fingerprints,
    compute_main_fingerprint,
    read_fingerprints,
    write_fingerprints,
    compute_exec_fingerprint,
    exec_pass_is_cached,
    record_exec_pass,
    record_exec_outcome,
    record_stale_fingerprint,
)


def _sweep_case_binary(build_dir: Path, module_name: str) -> None:
    """Drop the case's linked binary unless TPY_KEEP_TEST_BINARIES asks
    otherwise -- called on every non-failing exit path so dead executables
    never accumulate under tests/cases/."""
    if not KEEP_TEST_BINARIES:
        try:
            case_binary_path(build_dir, module_name).unlink(missing_ok=True)
        except OSError:
            pass


@pytest.mark.parametrize("case_dir, main_src", [
    pytest.param(case_dir, main_src, id=name)
    for name, case_dir, main_src in discover_cases()
])
def test_case(case_dir, main_src, request):
    expected_dir = case_dir / "expected"
    # Plugin-claimed extensions (e.g. `.pas`) strip alongside `.py` so
    # the module name matches what the compiler derives internally.
    # The set is asked from the same conftest helper that wires the
    # plugin into Compiler -- both sides agree on which extensions
    # a given test's frontend claims.
    module_name = get_module_name(
        main_src, plugin_extensions_for(main_src))
    build_dir = case_dir / "__tpyc__"
    is_error = case_dir.name.startswith("error_")
    is_panic = case_dir.name.startswith("panic_")
    is_warn = case_dir.name.startswith("warn_")
    force_exec = (
        request.config.getoption("--force-exec")
        or request.config.getoption("--clean")
        or UPDATE_EXPECTED
    )
    no_exec = request.config.getoption("--no-exec")
    # Cross toolchains auto-degrade to build-only: the binaries cannot run
    # here, so manual and CI invocations behave identically with no flag.
    build_only = request.config.getoption("--build-only") or exec_is_cross()

    # An error case's annotations are gated as a set (see
    # `error_case_annotation_problems`): at least one `error` leg, no `ok`.
    # Read off src/ alone, so it runs before update mode wipes the tree.
    src_dir = case_dir / "src"
    if is_error:
        problems = error_case_annotation_problems(src_dir)
        if problems:
            pytest.fail("\n".join(problems), pytrace=False)

    # In update mode, clear stale artifacts so nothing lingers from a previous run
    if UPDATE_EXPECTED:
        diag = expected_dir / "diag.txt"
        if diag.exists():
            diag.unlink()
        for subdir in ["include", "src"]:
            d = expected_dir / subdir
            if d.is_dir():
                shutil.rmtree(d)
        for fname in ("output.txt", "panic.txt", ".fingerprints"):
            f = expected_dir / fname
            if f.exists():
                f.unlink()

    # ----- COMP PHASE ---------------------------------------------------------
    result = compile_with_diagnostics(
        main_src, build_dir, default_int=get_case_default_int(case_dir),
        snapshot_lib_modules=get_case_snapshot_lib_modules(case_dir),
    )

    # Diagnostics snapshot
    expected_diag = expected_dir / "diag.txt"
    check_or_update(result.diagnostics, expected_diag, "Diagnostics")

    # Inline # tpyc: annotations must match diagnostics. Validated in update
    # mode too, against the diagnostics just snapshotted: an update run that
    # skipped this rewrote diag.txt around a wrong annotation and left the
    # NEXT plain run to fail on it.
    all_annotation_errors: list[str] = []
    for src_file in sorted(src_dir.rglob("*.py")):
        all_annotation_errors.extend(validate_annotations(src_file, result.diagnostics))
    fail_annotations(request, all_annotation_errors)

    # Handle compile failure
    if not result.success:
        if not UPDATE_EXPECTED and not is_error:
            pytest.fail(
                f"Non-error test failed to compile: {main_src}\n"
                f"--- diagnostics ---\n{result.diagnostics}",
                pytrace=False,
            )
        # Error cases stop here: no code to verify, no runtime to run
        return

    # Error tests must NOT compile successfully -- in update mode too: the
    # only snapshot an error case owns is diag.txt, already written, and
    # running on would regenerate a code tree and output for a case that
    # asserts a rejection.
    if is_error:
        pytest.fail(
            f"Error test compiled successfully (expected compilation failure): {main_src}",
            pytrace=False,
        )

    def _snapshot_pairs(hpp_path, cpp_path) -> list[tuple[str, Path]]:
        pairs: list[tuple[str, Path]] = []
        if hpp_path is not None:  # None for native_module
            pairs.append((".hpp", hpp_path))
            # Cycle members get a sibling `<mod>_fwd.hpp`. It only exists for
            # actual cycle peers, so probe the filesystem rather than threading
            # a separate compiler-side flag.
            fwd_path = hpp_path.with_name(hpp_path.stem + "_fwd.hpp")
            if fwd_path.exists():
                pairs.append(("_fwd.hpp", fwd_path))
            # Modules with generators get `<mod>_inl.hpp` (inline __next__
            # bodies), probed the same way.
            inl_path = hpp_path.with_name(hpp_path.stem + "_inl.hpp")
            if inl_path.exists():
                pairs.append(("_inl.hpp", inl_path))
        if cpp_path is not None:
            pairs.append((".cpp", cpp_path))
        return pairs

    # The emitted artifact -- every local module, plus any library module the
    # case's options.json names, whose emission at these options and these
    # instantiations no import-only sweep reproduces -- byte-compared to the
    # expected files. This is the C++ exec builds, so a divergence here is a
    # divergence in what ships.
    snapshotted: set[Path] = set()
    for mod_name, hpp_path, cpp_path, is_local in result.all_modules:
        if not is_local and mod_name not in result.snapshot_lib_modules:
            continue
        for ext, gen_path in _snapshot_pairs(hpp_path, cpp_path):
            expected_file = module_to_expected_path(expected_dir, mod_name, ext)
            snapshotted.add(expected_file)
            if not gen_path.exists():
                pytest.fail(f"{gen_path} not generated", pytrace=False)
            check_or_update(gen_path.read_text(), expected_file,
                            f"{mod_name}{ext}", name_function=True)

    # The snapshotted set is derived from what compiled, so a module that was
    # renamed, dropped, or dropped out of a snapshot_lib_modules pattern leaves
    # its file behind with nothing comparing it -- coverage narrows in silence
    # and only a set comparison sees it. (Update mode wipes both dirs first, so
    # it cannot produce an orphan.)
    if not UPDATE_EXPECTED:
        on_disk = {p for sub in ("include", "src")
                   if (expected_dir / sub).is_dir()
                   for p in (expected_dir / sub).rglob("*") if p.is_file()}
        orphans = sorted(str(p.relative_to(expected_dir))
                         for p in on_disk - snapshotted)
        if orphans:
            pytest.fail(
                f"{len(orphans)} file(s) under {expected_dir} that nothing "
                f"this case compiles emits:\n"
                + "\n".join(f"  {p}" for p in orphans[:20])
                + "\nDelete them, or restore whatever used to emit them. "
                  "`uv run python tests/update_snapshots.py -k <case>` "
                  "rewrites the tree from scratch.",
                pytrace=False)

    # Additional semantic annotations. These read compile facts and feed no
    # snapshot, so a wrong one cannot corrupt a regeneration; they keep the
    # update-mode exemption CLAUDE.md documents.
    if not UPDATE_EXPECTED:
        if result.declared_var_types is not None:
            errs = validate_type_annotations(main_src, result.declared_var_types)
            if errs:
                pytest.fail("\n".join(errs), pytrace=False)
        if result.ptr_deref_facts is not None:
            errs = validate_non_null_annotations(main_src, result.ptr_deref_facts)
            if errs:
                pytest.fail("\n".join(errs), pytrace=False)
        if result.subscript_bounds_facts is not None:
            errs = validate_bounds_annotations(main_src, result.subscript_bounds_facts)
            if errs:
                pytest.fail("\n".join(errs), pytrace=False)
        if result.div_zero_facts is not None:
            errs = validate_div_annotations(main_src, result.div_zero_facts)
            if errs:
                pytest.fail("\n".join(errs), pytrace=False)
        if result.cast_safe_facts is not None:
            errs = validate_cast_annotations(main_src, result.cast_safe_facts)
            if errs:
                pytest.fail("\n".join(errs), pytrace=False)
        if result.send_sync_facts is not None:
            errs = validate_send_sync_annotations(main_src, result.send_sync_facts)
            if errs:
                pytest.fail("\n".join(errs), pytrace=False)
        if result.frame_facts is not None:
            errs = validate_frame_annotations(main_src, result.frame_facts)
            if errs:
                pytest.fail("\n".join(errs), pytrace=False)
        if result.mir_verdicts is not None:
            errs = validate_mir_annotations(main_src, result.mir_verdicts)
            if errs:
                pytest.fail("\n".join(errs), pytrace=False)
            if result.mir_line_facts is not None:
                errs = validate_mir_fact_annotations(main_src, result.mir_verdicts, result.mir_line_facts)
                if errs:
                    pytest.fail("\n".join(errs), pytrace=False)

    # ----- EXEC PHASE ---------------------------------------------------------

    # Entry native_module has no generated .cpp -- nothing to build/run
    entry_cpp = next(
        (cpp for name, _, cpp, _ in result.all_modules if name == module_name),
        None,
    )
    if entry_cpp is None:
        return

    # cpy phase still skips via committed fingerprints (CPython output is
    # toolchain-independent, so a committed key is portable); exec does not.
    cpy_stubs_unchanged = (
        compute_session_fingerprints()["cpy_stubs"]
        == read_session_fingerprints().get("cpy_stubs")
    )
    case_fps = read_fingerprints(case_dir)

    expected_runtime_file = expected_dir / ("panic.txt" if is_panic else "output.txt")

    # Exec is gated on a local, content-addressed pass marker (see conftest):
    # skip only when this exact build already ran green on this machine's
    # toolchain -- a committed fingerprint can't attest the build worked here.
    # get_stdlib_cache() is memoized + prewarmed in pytest_configure, so
    # fetching it here (rather than only in the build branch below) is cheap.
    # Under --no-exec nothing builds, so skip it -- building the .o set here
    # would need a C++ toolchain the exec-free run doesn't require.
    stdlib_cache = None if no_exec else get_stdlib_cache()
    # System-mode deps (--dep-mode) are not compiled into the stdlib cache;
    # the cache's link flags must join the case's own (empty in bundled mode).
    tp_link_flags = merge_link_flags(
        result.third_party_link_flags,
        stdlib_cache.link_flags if stdlib_cache else [])
    # Build-only skips the fingerprint entirely: it neither reads the marker
    # (its answer must be a current build) nor records one (built != ran green).
    exec_fp = ""
    marker_hit = False
    if not no_exec and not build_only:
        exec_fp = compute_exec_fingerprint(
            case_dir, result.all_modules,
            result.link_flags, tp_link_flags,
            stdlib_output_hash=stdlib_cache.output_hash if stdlib_cache else "",
        )
        marker_hit = exec_pass_is_cached(exec_fp)
    plan = plan_exec_phase(no_exec=no_exec, build_only=build_only,
                           force_exec=force_exec,
                           expected_exists=expected_runtime_file.exists(),
                           marker_hit=marker_hit)

    if plan == "disabled":
        record_exec_outcome("disabled")
    elif plan in ("build", "build+run"):
        all_cpp_files = [cpp for _, _, cpp, _ in result.all_modules if cpp is not None]
        extra_src = find_extra_src_files(case_dir)
        extra_includes = find_extra_include_dirs(case_dir)
        force_includes = find_force_includes(case_dir)
        cache = stdlib_cache
        # Combine test-supplied include dirs with third-party-resolved ones
        # (e.g. PCRE2 src dir for tests that import the `re` module).
        all_extra_includes = list(extra_includes) + list(result.third_party_include_dirs)
        # Third-party C sources (e.g. PCRE2 .c) are pre-compiled into the
        # stdlib cache; don't re-compile per-test or we'd duplicate symbols.
        per_test_c_sources = None if cache else (result.third_party_c_sources or None)
        run_result = build_and_run(
            build_dir, module_name,
            all_cpp_files=all_cpp_files,
            extra_src_files=extra_src or None,
            extra_include_dirs=all_extra_includes or None,
            force_includes=force_includes or None,
            link_flags=result.link_flags or None,
            precompiled_objects=cache.objects if cache else None,
            exclude_cpp_relpaths=cache.cpp_relpaths if cache else None,
            extra_link_flags=tp_link_flags or None,
            c_sources=per_test_c_sources,
            run=(plan == "build+run"),
        )
        if run_result.cpp_build_failed:
            pytest.fail(
                f"C++ compilation failed for {main_src}.\n"
                f"--- stderr ---\n{run_result.stderr}",
                pytrace=False,
            )
        if plan == "build":
            # Linked cleanly -- build-only's whole signal. Nothing ran, so
            # there is no output to compare and no pass marker to record.
            record_exec_outcome("built")
        elif run_result.success:
            check_or_update(run_result.stdout, expected_dir / "output.txt", "Output")
        else:
            if not UPDATE_EXPECTED and not is_panic:
                pytest.fail(
                    f"Non-panic test panicked for {main_src} (exit code {run_result.returncode}).\n"
                    f"--- stderr ---\n{run_result.stderr}",
                    pytrace=False,
                )
            check_or_update(run_result.stderr, expected_dir / "panic.txt", "Panic output")
            # Panic tests may also print on stdout before the panic
            # (tpy_terminate_handler flushes stdout). Capture that
            # too so pre-panic side effects are observable. Guard on
            # either side having content so empty-stdout panic tests
            # don't generate stub files, but a regression that drops
            # stdout from a test that previously had observable
            # pre-panic output is still caught.
            if run_result.stdout or (expected_dir / "output.txt").exists():
                check_or_update(run_result.stdout, expected_dir / "output.txt", "Output")
        if plan == "build+run":
            # All comparisons above raise on mismatch, so reaching here means
            # the build+run reproduced the expected output: cache the pass so
            # unchanged cases skip exec on the next run without --force-exec.
            record_exec_pass(exec_fp, request.node.name)
            record_exec_outcome("ran")
    else:
        record_exec_outcome("skipped")
        if not is_warn and not (expected_dir / "output.txt").exists() and not is_panic:
            # Compiled cleanly but no expected output recorded; warn so user can
            # run update_snapshots.py -k <case>
            warnings.warn(
                f"Test '{case_dir.name}' compiles but has no expected/output.txt "
                f"(run update_snapshots.py -k {case_dir.name})",
                stacklevel=1,
            )

    # Exec checks passed (failures raise above): drop the linked binary
    # unless explicitly kept. Rebuilds don't read it, so retaining it only
    # accumulates dead executables across the corpus. Swept on the
    # marker-cache skip path too, so binaries left by older harness versions
    # (or by runs with TPY_KEEP_TEST_BINARIES=1) disappear on the next suite
    # run.
    _sweep_case_binary(build_dir, module_name)

    # ----- CPY PHASE ----------------------------------------------------------

    ran_cpy = False
    current_main_fp: str | None = None
    cpy_applicable = cpy_phase_applicable(
        build_only=build_only,
        no_cpy=request.config.getoption("--no-cpy"),
        is_panic=is_panic,
        no_cpython_marker=(case_dir / "no_cpython.txt").exists(),
        output_exists=(expected_dir / "output.txt").exists(),
    )
    if cpy_applicable:
        current_main_fp = compute_main_fingerprint(main_src)
        main_unchanged = case_fps.get("main") == current_main_fp
        can_skip_cpy = (
            not force_exec
            and cpy_stubs_unchanged
            and main_unchanged
        )
        if not can_skip_cpy:
            ran_cpy = True
            cpython_output = run_cpython(main_src)
            # Cpy never writes output.txt -- the exec phase is the source of
            # truth. Even in UPDATE_EXPECTED, cpy must agree with what exec
            # wrote, otherwise the test fails with a clear diff.
            check_or_update(
                cpython_output,
                expected_dir / "output.txt",
                "CPython output",
                compare_only=True,
            )

        # Warn when the per-case main fingerprint is stale. The next non-
        # forced run will re-execute cpy until it's refreshed.
        # Skip the warning when only the session-level cpy_stubs hash was stale --
        # that's already surfaced once at session start, no need to repeat per case.
        if (
            ran_cpy
            and not UPDATE_EXPECTED
            and cpy_stubs_unchanged
            and not main_unchanged
        ):
            record_stale_fingerprint(case_dir.name)
            warnings.warn(
                f"Test '{case_dir.name}': main fingerprint stale -- "
                f"the next non-forced run will re-execute cpy. "
                f"Refresh via update_snapshots.py -k {case_dir.name}",
                stacklevel=1,
            )

    # ----- Record per-case fingerprint in update mode ------------------------
    # The only committed per-case fingerprint left is `main`, which gates the
    # (toolchain-independent) cpy phase. Exec inputs are tracked by the local
    # exec cache instead, so they're no longer recorded here. Session-level
    # fingerprints are written once on master by pytest_configure.
    if UPDATE_EXPECTED:
        new_fps: dict[str, str] = {}
        if ran_cpy and current_main_fp is not None:
            new_fps["main"] = current_main_fp
        # write_fingerprints removes the file when new_fps is empty, so cases
        # with no cpy phase end up with no per-case fingerprint file.
        write_fingerprints(case_dir, new_fps)
