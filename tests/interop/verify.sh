#!/usr/bin/env bash
# Verify every CPython-extension interop case under tests/interop/<case>/.
# Each case directory holds an `# tpy: ext_module` source plus driver.py (and
# optionally ext_checks.py). For the whole set this proves:
#   - the facade layout self-check passes against the real Python ABI (once)
# and per case:
#   - tpyc emits the module .cpp + glue TU
#   - it builds to a .so (facade only -- no Python.h, no libpython)
#   - ext-exec: CPython imports the .so and runs driver.py
#   - cpy-parity: the SAME driver over the TPy source (lib/cpy stubs) matches
#   - ext_checks.py (ext-only marshalling-error cases) passes, if present
#
# Interim until the snapshot-harness ext-exec variant lands.
# Run from anywhere:  bash tests/interop/verify.sh
# Requires a C++ toolchain, Python dev headers (Python.h), CPython >= 3.12.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
CXX="${CXX:-g++}"
PYINC="$(uv run python -c 'import sysconfig; print(sysconfig.get_path("include"))')"

echo "== facade layout self-check vs real Python ABI =="
SC="$(mktemp -d)"; trap 'rm -rf "$SC"' EXIT
"$CXX" -std=c++23 -DPy_LIMITED_API=0x030c0000 \
  -I runtime/cpp/include -I "$PYINC" \
  -c runtime/cpp/tests/interop/cpython_facade_selfcheck.cpp -o "$SC/sc.o"

fail=0
for case_dir in tests/interop/*/; do
  [[ -f "${case_dir}driver.py" ]] || continue   # skips build/ (no driver.py)
  name="$(basename "$case_dir")"
  mod_py="$(grep -l '# tpy: ext_module' "${case_dir}"*.py)"
  mod="$(basename "$mod_py" .py)"
  echo "== case ${name} (module: ${mod}) =="

  # Persistent, gitignored build dir so the generated C++ + .so stay
  # inspectable after a run (NOT a deleted mktemp).
  build="tests/interop/build/${name}"
  rm -rf "$build"; mkdir -p "$build/built"
  if ! uv run tpyc "$mod_py" -o "$build/out" --no-main >"$build/tpyc.log" 2>&1; then
    echo "  FAIL: tpyc emit"; cat "$build/tpyc.log"; fail=1; continue
  fi
  mapfile -t CPPS < <(find "$build/out/src" -name '*.cpp')
  "$CXX" -std=c++23 -O2 -fPIC -shared \
    -I "$build/out/include" -I "$build/out/runtime/include" \
    "${CPPS[@]}" -o "$build/built/${mod}.so"

  if ldd "$build/built/${mod}.so" | grep -qi python; then
    echo "  FAIL: .so links libpython"; fail=1; continue
  fi

  cp "${case_dir}driver.py" "$build/built/"
  [[ -f "${case_dir}ext_checks.py" ]] && cp "${case_dir}ext_checks.py" "$build/built/"

  ext_out="$(cd "$build/built" && uv run --project "$ROOT" python driver.py)"
  cpy_out="$(PYTHONPATH="$ROOT/lib/cpy:$ROOT/${case_dir}" uv run python "$ROOT/${case_dir}driver.py")"
  if [[ "$ext_out" != "$cpy_out" ]]; then
    echo "  FAIL: ext-exec != cpy-parity"; diff <(echo "$ext_out") <(echo "$cpy_out") || true
    fail=1; continue
  fi

  if [[ -f "${case_dir}ext_checks.py" ]]; then
    (cd "$build/built" && uv run --project "$ROOT" python ext_checks.py)
  fi
  echo "  PASS (ext-exec == cpy-parity)"
  echo "       sources: ${build}/out/src/   so: ${build}/built/${mod}.so"
done

if [[ $fail -eq 0 ]]; then
  echo "INTEROP VERIFY: PASS"
else
  echo "INTEROP VERIFY: FAIL"; exit 1
fi
