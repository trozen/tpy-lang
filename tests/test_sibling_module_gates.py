"""A case's sibling modules (src/*.py beside main.py) are checked like main.py.

The comp-only `# tpyc:` annotations are validated in every local module
against that module's own facts, and the committed cpy fingerprint covers
every source the CPython run reads. A passing case proves only the accepting
side; a wrong annotation or a stale sibling hash has no case that can carry
it, so both refusing sides are pinned here.
"""
import hashlib
from pathlib import Path

from conftest import DEFAULT_LIB_DIRS, CompileResult, case_request, compile_case, compute_cpy_fingerprints
from tpyc.compiler import Compiler


def _compile(tmp_path: Path, files: dict[str, str]) -> CompileResult:
    src = tmp_path / "case" / "src"
    src.mkdir(parents=True)
    for name, text in files.items():
        (src / name).write_text(text)
    req = case_request(src / "main.py", tmp_path / "case" / "__tpyc__")
    compiler = Compiler(req.src_file, default_int=req.default_int, lib_dirs=list(DEFAULT_LIB_DIRS))
    return compile_case(compiler, compiler.compile, req)


_MAIN = (
    "import part\n"
    "def main() -> None:\n"
    "    t = 'entry'  # tpyc: type(StrView)\n"
    "    print(t)\n"
    "    part.main()\n"
    "main()\n"
)


def test_wrong_annotations_in_a_sibling_are_reported_against_its_own_facts(tmp_path):
    result = _compile(tmp_path, {"main.py": _MAIN, "part.py": (
        "def main() -> None:  # tpyc: mir(conflict /nothing/)\n"
        "    xs = [1, 2]\n"
        # Same line as main.py's StrView local: the sibling's own fact must answer.
        "    n = len(xs)  # tpyc: type(float)\n"
        "    if n != 0:\n"
        "        print(xs[0], 6 // n)  # tpyc: div_checked(n)\n"
    )})
    assert result.success, result.diagnostics
    assert result.annotation_errors == [
        "part.py: Line 3: expected type 'float' for 'n' but got 'int32'",
        "part.py: Line 5: expected 'n' divisor to be div_checked but zero check is skipped (proven non-zero)",
        "part.py: Line 1: expected mir(conflict /nothing/) for 'main' but MIR says covered "
        "(storage: no_proof_required), summary: opaque (summary output effect)",
    ]


def test_every_wrong_kind_in_a_sibling_is_reported(tmp_path):
    result = _compile(tmp_path, {"main.py": _MAIN, "part.py": (
        "from typing import Iterator\n"
        "from tpy import Ptr, int8, int32\n"
        "class P:\n"
        "    def __init__(self, x: int32) -> None:\n"
        "        self.x = x\n"
        "def gen(n: int32) -> Iterator[int32]:  # tpyc: frame_send(no)\n"
        "    yield n\n"
        "def at(xs: list[int32], i: int32) -> int32:\n"
        "    return xs[i]  # tpyc: bounds_safe(xs)\n"
        "def narrow(v: int32) -> int8:\n"
        "    return int8(v)  # tpyc: cast_safe(int8)\n"
        "def show(p: Ptr[P]) -> None:\n"
        "    if p is not None:\n"
        "        print(p.x)  # tpyc: nullable(p)\n"
        "def joined(a: str, b: str) -> int32:\n"
        "    t = a + b  # tpyc: mir_borrowed(t)\n"
        "    return len(t)\n"
        "def main() -> None:\n"
        "    k = 3  # tpyc: is_sync(no)\n"
        "    print(k, at([1], 0), narrow(1), joined('a', 'b'))\n"
        "    for v in gen(1):\n"
        "        print(v)\n"
    )})
    assert result.success, result.diagnostics
    errors = result.annotation_errors
    assert "part.py: Line 6: expected frame_send(no) for 'gen' but compiler says frame_send(yes)" in errors
    assert "part.py: Line 9: expected 'xs' subscript to be bounds_safe but bounds check is used" in errors
    assert "part.py: Line 11: expected cast to 'int8' to be cast_safe but range check is used" in errors
    assert "part.py: Line 14: expected 'p' to be nullable but deref_check is skipped (proven non-null)" in errors
    assert "part.py: Line 16: expected mir_borrowed(t) in 'joined' but MIR writes t as owned" in errors
    assert "part.py: Line 19: expected is_sync(no) for 'k' but compiler says is_sync(yes)" in errors
    assert len(errors) == 6, errors


def test_correct_annotations_in_a_sibling_pass(tmp_path):
    result = _compile(tmp_path, {"main.py": _MAIN, "part.py": (
        "def main() -> None:\n"
        "    xs = [1, 2]\n"
        "    n = len(xs)  # tpyc: type(int32)\n"
        "    if n != 0:\n"
        "        print(xs[0], 6 // n)  # tpyc: div_safe(n)\n"
    )})
    assert result.success, result.diagnostics
    assert result.annotation_errors == []


def _src(tmp_path: Path, files: dict[str, str]) -> Path:
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    for name, text in files.items():
        path = src / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return src / "main.py"


def test_single_file_case_has_only_the_main_key(tmp_path):
    main = _src(tmp_path, {"main.py": "print(1)\n"})
    assert compute_cpy_fingerprints(main) == {"main": hashlib.sha256(b"print(1)\n").hexdigest()}


def test_a_sibling_change_moves_cpy_src_and_keeps_main(tmp_path):
    main = _src(tmp_path, {"main.py": "import part\n", "part.py": "print(1)\n"})
    before = compute_cpy_fingerprints(main)
    assert before["main"] == hashlib.sha256(b"import part\n").hexdigest()
    _src(tmp_path, {"part.py": "print(2)\n"})
    after = compute_cpy_fingerprints(main)
    assert after["main"] == before["main"]
    assert after["cpy_src"] != before["cpy_src"]


def test_cpy_src_covers_nested_packages_and_the_stdin_fixture(tmp_path):
    main = _src(tmp_path, {"main.py": "import pkg.helper\n", "pkg/helper.py": "x = 1\n"})
    before = compute_cpy_fingerprints(main)["cpy_src"]
    _src(tmp_path, {"pkg/helper.py": "x = 2\n"})
    nested = compute_cpy_fingerprints(main)["cpy_src"]
    assert nested != before
    _src(tmp_path, {"input.txt": "line\n"})
    assert compute_cpy_fingerprints(main)["cpy_src"] != nested


def test_cpy_src_changes_when_the_stdin_fixture_contents_change(tmp_path):
    main = _src(tmp_path, {"main.py": "print(input())\n", "input.txt": "a\n"})
    before = compute_cpy_fingerprints(main)["cpy_src"]
    _src(tmp_path, {"input.txt": "b\n"})
    assert compute_cpy_fingerprints(main)["cpy_src"] != before


def test_main_with_a_stdin_fixture_has_both_keys(tmp_path):
    main = _src(tmp_path, {"main.py": "print(input())\n", "input.txt": "a\n"})
    assert set(compute_cpy_fingerprints(main)) == {"main", "cpy_src"}
