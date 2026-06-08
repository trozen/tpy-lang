"""Unified test case runner: compile + optional exec + optional CPython verification.

Each folder under tests/cases/ becomes one test item. The test always runs the
COMP phase (compile, snapshot check, annotation validation). The EXEC and CPY
phases auto-skip when generated code matches expected AND the recorded input
fingerprint is unchanged -- in that state the binary and CPython would
reproduce the same stdout, so re-running adds no signal.

Flags:
    --force-exec         Run exec/cpy phases unconditionally (bypass auto-skip).
    --clean              Wipe shared PCH + stdlib .o caches; implies --force-exec.
    --update-snapshots   Regenerate expected files + fingerprints; implies
                         --force-exec. Equivalent to UPDATE_EXPECTED=1.
    UPDATE_EXPECTED=1    Env-var form of --update-snapshots (for CI / wrappers).
"""

import shutil
import warnings
from pathlib import Path

import pytest

from conftest import (
    UPDATE_EXPECTED,
    get_module_name,
    plugin_extensions_for,
    compile_with_diagnostics,
    get_case_default_int,
    validate_annotations,
    validate_type_annotations,
    validate_non_null_annotations,
    validate_bounds_annotations,
    validate_div_annotations,
    validate_cast_annotations,
    validate_send_sync_annotations,
    validate_frame_annotations,
    parse_annotations,
    check_or_update,
    discover_cases,
    find_extra_src_files,
    find_extra_include_dirs,
    find_force_includes,
    build_and_run,
    get_stdlib_cache,
    run_cpython,
    compute_session_fingerprints,
    read_session_fingerprints,
    compute_extra_src_fingerprint,
    compute_main_fingerprint,
    read_fingerprints,
    write_fingerprints,
)


def _module_to_expected_path(expected_dir: Path, mod_name: str, ext: str) -> Path:
    """Convert module name to expected file path for generated code snapshots.

    Recognizes both the `.hpp` / `.cpp` extensions and the `_fwd.hpp`
    cycle-peer forward-declaration header suffix (lives under include/).
    """
    subdir = "src" if ext == ".cpp" else "include"
    parts = mod_name.split('.')
    if len(parts) == 1:
        return expected_dir / subdir / f"{parts[0]}{ext}"
    rel_dir = '/'.join(parts[:-1])
    return expected_dir / subdir / rel_dir / f"{parts[-1]}{ext}"


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
        main_src, build_dir, default_int=get_case_default_int(case_dir)
    )

    # Diagnostics snapshot
    expected_diag = expected_dir / "diag.txt"
    check_or_update(result.diagnostics, expected_diag, "Diagnostics")

    # Inline # tpyc: annotations must match diagnostics
    if not UPDATE_EXPECTED:
        src_dir = case_dir / "src"
        all_annotation_errors: list[str] = []
        for src_file in sorted(src_dir.rglob("*.py")):
            all_annotation_errors.extend(validate_annotations(src_file, result.diagnostics))
        if all_annotation_errors:
            pytest.fail("\n".join(all_annotation_errors), pytrace=False)

    # Error tests must carry at least one error annotation
    if not UPDATE_EXPECTED and is_error:
        src_dir = case_dir / "src"
        has_error_annotation = False
        for src_file in src_dir.rglob("*.py"):
            annotations = parse_annotations(src_file.read_text())
            if any(a.level == "error" for a in annotations):
                has_error_annotation = True
                break
        if not has_error_annotation:
            pytest.fail(
                "Error test must have at least one '# tpyc: error(...)' annotation",
                pytrace=False,
            )

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

    # Error tests must NOT compile successfully
    if not UPDATE_EXPECTED and is_error:
        pytest.fail(
            f"Error test compiled successfully (expected compilation failure): {main_src}",
            pytrace=False,
        )

    # Generated code snapshots (.hpp / .cpp) for every local module
    for mod_name, hpp_path, cpp_path, is_local in result.all_modules:
        if not is_local:
            continue
        pairs: list[tuple[str, Path]] = []
        if hpp_path is not None:  # None for native_module
            pairs.append((".hpp", hpp_path))
            # Cycle members get a sibling `<mod>_fwd.hpp`. It only
            # exists for actual cycle peers, so probe the filesystem
            # rather than threading a separate compiler-side flag.
            fwd_path = hpp_path.with_name(hpp_path.stem + "_fwd.hpp")
            if fwd_path.exists():
                pairs.append(("_fwd.hpp", fwd_path))
        if cpp_path is not None:
            pairs.append((".cpp", cpp_path))
        for ext, gen_path in pairs:
            expected_file = _module_to_expected_path(expected_dir, mod_name, ext)
            if not gen_path.exists():
                pytest.fail(f"{gen_path} not generated", pytrace=False)
            check_or_update(gen_path.read_text(), expected_file, f"{mod_name}{ext}")

    # Additional semantic annotations
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

    # ----- EXEC PHASE ---------------------------------------------------------

    # Entry native_module has no generated .cpp -- nothing to build/run
    entry_cpp = next(
        (cpp for name, _, cpp, _ in result.all_modules if name == module_name),
        None,
    )
    if entry_cpp is None:
        return

    session_now = compute_session_fingerprints()
    session_recorded = read_session_fingerprints()
    runtime_unchanged = session_now["runtime"] == session_recorded.get("runtime")
    libtpy_unchanged = session_now["libtpy"] == session_recorded.get("libtpy")
    cpy_stubs_unchanged = session_now["cpy_stubs"] == session_recorded.get("cpy_stubs")

    case_fps = read_fingerprints(case_dir)
    current_extra_src_fp = compute_extra_src_fingerprint(case_dir)  # None when no companions
    extra_src_unchanged = case_fps.get("extra_src") == current_extra_src_fp  # both-None matches

    expected_runtime_file = expected_dir / ("panic.txt" if is_panic else "output.txt")

    can_skip_exec = (
        not force_exec
        and expected_runtime_file.exists()
        and runtime_unchanged
        and libtpy_unchanged
        and extra_src_unchanged
    )

    ran_exec = False
    if not can_skip_exec:
        ran_exec = True
        all_cpp_files = [cpp for _, _, cpp, _ in result.all_modules if cpp is not None]
        extra_src = find_extra_src_files(case_dir)
        extra_includes = find_extra_include_dirs(case_dir)
        force_includes = find_force_includes(case_dir)
        cache = get_stdlib_cache()
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
            extra_link_flags=result.third_party_link_flags or None,
            c_sources=per_test_c_sources,
        )
        if run_result.cpp_build_failed:
            pytest.fail(
                f"C++ compilation failed for {main_src}.\n"
                f"--- stderr ---\n{run_result.stderr}",
                pytrace=False,
            )
        if run_result.success:
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
    elif not is_warn and not (expected_dir / "output.txt").exists() and not is_panic:
        # Compiled cleanly but no expected output recorded; warn so user can
        # run update_snapshots.py -k <case>
        warnings.warn(
            f"Test '{case_dir.name}' compiles but has no expected/output.txt "
            f"(run update_snapshots.py -k {case_dir.name})",
            stacklevel=1,
        )

    # Warn when exec re-ran due to per-case extra_src fingerprint mismatch
    # (test passed, but next run will re-execute until fingerprint is refreshed).
    if (
        ran_exec
        and not UPDATE_EXPECTED
        and not force_exec
        and not extra_src_unchanged
    ):
        warnings.warn(
            f"Test '{case_dir.name}': extra_src fingerprint stale -- "
            f"exec re-ran and passed, but next invocation will re-execute. "
            f"Refresh via update_snapshots.py -k {case_dir.name}",
            stacklevel=1,
        )

    # ----- CPY PHASE ----------------------------------------------------------

    ran_cpy = False
    current_main_fp: str | None = None
    cpy_applicable = (
        not is_panic
        and not (case_dir / "no_cpython.txt").exists()
        and (expected_dir / "output.txt").exists()
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
            warnings.warn(
                f"Test '{case_dir.name}': main fingerprint stale -- "
                f"the next non-forced run will re-execute cpy. "
                f"Refresh via update_snapshots.py -k {case_dir.name}",
                stacklevel=1,
            )

    # ----- Record per-case fingerprints in update mode -----------------------
    # Session-level fingerprints (runtime, cpy_stubs) are written once on the
    # master process by pytest_configure -- nothing to do per case for those.
    if UPDATE_EXPECTED:
        new_fps: dict[str, str] = {}
        if ran_exec and current_extra_src_fp is not None:
            new_fps["extra_src"] = current_extra_src_fp
        if ran_cpy and current_main_fp is not None:
            new_fps["main"] = current_main_fp
        # write_fingerprints removes the file when new_fps is empty, so cases
        # with no per-case state (no extra_src, no cpy) end up with no file
        write_fingerprints(case_dir, new_fps)
