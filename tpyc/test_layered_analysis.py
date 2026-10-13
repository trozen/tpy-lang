"""A program's diagnostics and C++ do not depend on how `Compiler.prepare`
splits its modules into import-ordered analysis layers; a prepared compiler
refuses a program whose directory changes what the layers resolved."""

from pathlib import Path

import pytest

from . import get_lib_dir
from .codegen_cpp import CodeGenOptions
from .compiler import CompiledModule, Compiler, LayerReuseRefused
from .diagnostics import format_diagnostics
from .modules.resolver import ModuleResolver, resolution_drift

_LIB_DIRS = [get_lib_dir() / "tpy"]

# Two local modules, one of them generic, an `async def` and asyncio: every
# layer the asyncio split prepares has something to analyze.
_LOCAL_MODULES = {
    "shapes.py": (
        "from tpy import int32\n"
        "\n"
        "\n"
        "class Box[T]:\n"
        "    def __init__(self, v: T) -> None:\n"
        "        self.v = v\n"
        "\n"
        "\n"
        "def area(w: int32, h: int32) -> int32:\n"
        "    return w * h\n"),
    "tasks.py": (
        "import asyncio\n"
        "from tpy import int32\n"
        "from shapes import Box, area\n"
        "\n"
        "\n"
        "async def measure(b: Box[int32]) -> int32:\n"
        "    await asyncio.sleep(0)\n"
        "    return area(b.v, 2)\n"),
    "main.py": (
        "import asyncio\n"
        "from shapes import Box\n"
        "from tasks import measure\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    print(asyncio.run(measure(Box(3))))\n"
        "\n"
        "\n"
        "main()\n"),
}

# asyncio and two implicit-stdlib modules no prepared layer holds: under the
# asyncio split `tpy.sync` / `tpy.atomic` are ordered after asyncio's
# closure, under a single program layer among it.
_LATE_IMPLICIT = {
    "main.py": (
        "import asyncio\n"
        "from tpy import uint32\n"
        "from tpy.atomic import Atomic\n"
        "from tpy.sync import Mutex\n"
        "\n"
        "\n"
        "async def bump(n: uint32) -> uint32:\n"
        "    await asyncio.sleep(0)\n"
        "    return n + 1\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    counter = Atomic[uint32](1)\n"
        "    counter.fetch_add(asyncio.run(bump(2)))\n"
        "    m = Mutex.new(counter.load())\n"
        "    with m.lock() as g:\n"
        "        print(g.get())\n"
        "\n"
        "\n"
        "main()\n"),
}


def _write(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (root / name).write_text(text)
    return root / "main.py"


def _outputs(compiler: Compiler, modules: list[CompiledModule]
             ) -> tuple[set[str], list[str], dict[str, tuple[str, str]]]:
    options = CodeGenOptions(emit_source_comments=True, comment_line_numbers=False)
    diags = [line for _, line in format_diagnostics(compiler, modules)]
    code = {m.name: compiler.generate_code_to_strings(m, options) for m in modules}
    return {m.name for m in modules}, diags, code


@pytest.mark.parametrize("files", [_LOCAL_MODULES, _LATE_IMPLICIT],
                         ids=["local_modules", "late_implicit"])
def test_layer_split_does_not_change_output(files, tmp_path):
    entry = _write(tmp_path, files)
    single = Compiler(entry, lib_dirs=list(_LIB_DIRS))
    expected = _outputs(single, single.compile())

    layered = Compiler(entry, lib_dirs=list(_LIB_DIRS))
    layered.prepare([["asyncio"]])
    assert "asyncio" in layered.compile_order and "main" not in layered.compile_order
    actual = _outputs(layered, layered.finish())

    # The compile ORDER may differ (each layer puts its own implicit-stdlib
    # modules first); the modules, diagnostics and C++ may not.
    assert actual[0] == expected[0]
    assert actual[1] == expected[1]
    for name in expected[2]:
        assert actual[2][name] == expected[2][name], name


def test_import_cycle_spanning_layers_asserts(tmp_path):
    (tmp_path / "base.py").write_text("def one() -> int:\n    return 1\n")
    (tmp_path / "top.py").write_text("import base\n")
    entry = tmp_path / "main.py"
    entry.write_text("import top\n")
    compiler = Compiler(entry, lib_dirs=list(_LIB_DIRS))
    compiler.prepare([["base"]])
    # An analyzed layer can only import itself or earlier layers; an edge
    # back from it to a later module would need both re-analyzed together.
    compiler.modules["base"].ast.user_module_imports["top"] = 1
    with pytest.raises(AssertionError, match="spans analysis layers"):
        compiler.finish()


def _prepared(tmp_path: Path) -> Compiler:
    template = tmp_path / "template"
    template.mkdir()
    compiler = Compiler(template / "__template__.py", lib_dirs=list(_LIB_DIRS))
    compiler.prepare()
    return compiler


def test_reuse_refuses_a_directory_satisfying_a_prepared_miss(tmp_path):
    # The implicit stdlib probes the name of an imported function as a
    # submodule, `tpy._builtins.sorted`, and misses; a namespace package in
    # the program's directory would satisfy it (`tpy` itself still resolves to
    # the stdlib: a directory without `__init__.py` is not a package).
    compiler = _prepared(tmp_path)
    assert compiler.resolver.resolution_log.get("tpy._builtins.sorted", "absent") is None
    program = tmp_path / "program"
    (program / "tpy" / "_builtins").mkdir(parents=True)
    (program / "tpy" / "_builtins" / "sorted.py").write_text("x = 1\n")
    entry = _write(program, {"main.py": "print(1)\n"})
    with pytest.raises(LayerReuseRefused,
                       match="'tpy._builtins.sorted' resolves to "
                             ".*program/tpy/_builtins/sorted.py, not nothing"):
        compiler.finish(entry, reuse=True)


def test_reuse_finishes_an_ordinary_program(tmp_path):
    compiler = _prepared(tmp_path)
    program = tmp_path / "program"
    program.mkdir()
    entry = _write(program, {"helper.py": "def one() -> int:\n    return 1\n",
                             "main.py": "import helper\n\nprint(helper.one())\n"})
    names = [m.name for m in compiler.finish(entry, reuse=True)]
    assert names[-2:] == ["helper", "main"]


def test_reuse_refuses_an_entry_named_like_a_prepared_module(tmp_path):
    compiler = _prepared(tmp_path)
    program = tmp_path / "program"
    program.mkdir()
    entry = program / "typing.py"
    entry.write_text("print(1)\n")
    with pytest.raises(LayerReuseRefused, match="named like the prepared 'typing'"):
        compiler.finish(entry, reuse=True)


def test_resolution_drift_reads_probes_under_the_new_base(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    resolver = ModuleResolver(new)
    probes = [str(old / "mymacro.py")]
    assert resolution_drift(resolver, {}, probes, recorded_base=old) is None
    (new / "mymacro.py").write_text("")
    assert resolution_drift(resolver, {}, probes, recorded_base=old) == (
        f"{new / 'mymacro.py'} exists")


def test_resolution_drift_keeps_a_resolved_module_at_its_file(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (old / "helper.py").write_text("")
    (new / "helper.py").write_text("")
    resolver = ModuleResolver(new)
    # the same relative path under the new base is another file: its analysis is not reusable
    assert resolution_drift(resolver, {"helper": str(old / "helper.py")}, [],
                            recorded_base=old) == (
        f"'helper' resolves to {new / 'helper.py'}, not {old / 'helper.py'}")
