"""Manual differential fuzzing for BigInt runtime behavior.

This test is intentionally opt-in only:
    RUN_MANUAL_BIGINT_FUZZ=1 uv run pytest tests/test_bigint_fuzz_manual.py

It generates deterministic random bigint operation cases, runs them through:
1) TurboPython compiled binary
2) CPython execution
and compares full stdout for exact parity.
"""

from __future__ import annotations

import os
import random

import pytest

from conftest import (
    build_and_run,
    compile_with_diagnostics,
    get_module_name,
    run_cpython,
)


def _enabled() -> bool:
    return os.environ.get("RUN_MANUAL_BIGINT_FUZZ", "").lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        pytest.fail(f"{name} must be an integer, got: {raw!r}")


def _edge_values() -> list[int]:
    b = 1 << 62
    return [
        0,
        1,
        -1,
        2,
        -2,
        b - 2,
        b - 1,
        b,
        b + 1,
        -b - 1,
        -b,
        -b + 1,
        (1 << 63) - 1,
        -(1 << 63),
        (1 << 70),
        -(1 << 70),
        (1 << 127),
        -(1 << 127),
        (1 << 200) + 1234567,
        -((1 << 200) + 7654321),
    ]


def _rand_bigint(rng: random.Random) -> int:
    # Bias toward boundary-heavy and very large values.
    mode = rng.randrange(6)
    if mode == 0:
        return rng.choice(_edge_values())
    if mode == 1:
        return rng.randint(-(1 << 20), (1 << 20))
    if mode == 2:
        return rng.randint(-(1 << 62), (1 << 62))
    if mode == 3:
        bits = rng.randint(63, 130)
    elif mode == 4:
        bits = rng.randint(131, 260)
    else:
        bits = rng.randint(261, 520)

    v = rng.getrandbits(bits)
    if rng.random() < 0.5:
        v = -v
    return v


@pytest.mark.skipif(not _enabled(), reason="manual test; set RUN_MANUAL_BIGINT_FUZZ=1")
def test_bigint_differential_fuzz_manual(tmp_path):
    seed = _env_int("BIGINT_FUZZ_SEED", 20260218)
    pairs = _env_int("BIGINT_FUZZ_PAIRS", 400)
    include_pow_cases = _env_int("BIGINT_FUZZ_POW_CASES", 120)
    rng = random.Random(seed)

    src_file = tmp_path / "bigint_fuzz.py"
    build_dir = tmp_path / "__tpyc__"

    lines: list[str] = [
        "# Deterministic differential fuzz for bigint runtime semantics.",
        "# Generated inside the manual pytest test.",
        "",
        "from tpy import Int32",
        "",
        "def emit_case_str(a_s: str, b_s: str, sh: Int32) -> None:",
        "    a: int = int(a_s)",
        "    b: int = int(b_s)",
        "    print(a)",
        "    print(b)",
        "    print(a + b)",
        "    print(a - b)",
        "    print(a * b)",
        "    if b != 0:",
        "        print(a // b)",
        "        print(a % b)",
        "    print(a & b)",
        "    print(a | b)",
        "    print(a ^ b)",
        "    print(~a)",
        "    print(a == b)",
        "    print(a != b)",
        "    print(a < b)",
        "    print(a <= b)",
        "    print(a > b)",
        "    print(a >= b)",
        "    print(a << sh)",
        "    print(a >> sh)",
        "",
    ]

    # Deterministic edge matrix first.
    edges = _edge_values()
    for a in edges:
        for b in edges[:10]:
            sh = (abs(a) + abs(b)) % 130
            lines.append(f"emit_case_str('{a}', '{b}', Int32({sh}))")

    # Random pair matrix.
    for _ in range(pairs):
        a = _rand_bigint(rng)
        b = _rand_bigint(rng)
        sh = rng.randint(0, 220)
        lines.append(f"emit_case_str('{a}', '{b}', Int32({sh}))")

    # Pow checks (non-negative exponents only; keep manageable to avoid huge runtime).
    lines.extend([
        "",
        "def emit_pow_str(base_s: str, exp: int) -> None:",
        "    base: int = int(base_s)",
        "    print(base)",
        "    print(exp)",
        "    print(base ** exp)",
        "",
    ])
    pow_bases = edges + [rng.randint(-1000, 1000) for _ in range(include_pow_cases)]
    for i, base in enumerate(pow_bases):
        if i < len(edges):
            exp = i % 12
        else:
            exp = rng.randint(0, 18)
        lines.append(f"emit_pow_str('{base}', {exp})")

    # Conversion checks through int() and fixed-width constructor paths.
    lines.extend([
        "",
        "print(int('  +1234567890123456789012345678901234567890  '))",
        "print(int(' -1234567890123456789012345678901234567890 '))",
        "print(int(1.9e20))",
        "print(int(-1.9e20))",
        "print(int(1e100))",
        "print(int(-1e100))",
    ])

    src_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Compile with BigInt default to maximize bigint-path exercise.
    result = compile_with_diagnostics(src_file, build_dir, default_int="BigInt")
    if not result.success:
        pytest.fail(f"Compilation failed.\nDiagnostics:\n{result.diagnostics}", pytrace=False)

    module_name = get_module_name(src_file)
    all_cpp_files = [cpp_path for _, _, cpp_path in result.all_modules] if result.all_modules else None
    run_result = build_and_run(build_dir, module_name, all_cpp_files=all_cpp_files)
    if run_result.cpp_build_failed:
        pytest.fail(f"C++ build failed.\nStderr:\n{run_result.stderr}", pytrace=False)
    if not run_result.success:
        pytest.fail(
            f"Runtime failed (exit code {run_result.returncode}).\nStderr:\n{run_result.stderr}",
            pytrace=False,
        )

    py_out = run_cpython(src_file)
    assert run_result.stdout == py_out
