"""Tests for compiler and build layout."""

from pathlib import Path

import pytest

from . import get_lib_dir, get_runtime_dir
from .compiler import Compiler, BuildLayout, CompileError
from .compilation_context import activate_compiler
from .codegen_cpp import CodeGenOptions
from .diagnostics import DiagnosticLevel, SemanticError
from .frontend_ir import FrontendModule, Import
from .frontend_plugin import (
    FrontendOutput, FrontendPlugin, FrontendRegistry, WorkspaceContext,
)
from .parse import TpyImport
from .thir.lower import iter_module_callables
from .thir.reject import is_bodyless_binding
from .typesys import (
    INT32, INT64, BIGINT, BOOL, FLOAT, STR, STRVIEW, CHAR, VOID, BYTEARRAY,
    PtrType, make_span, make_list, make_dict, make_array, OptionalType,
    TupleType, UnionType, OwnType, ReadonlyType, NominalType, CallableType,
    SendType, SyncType, make_send_marker, make_sync_marker, make_fn_type,
    MarkerAssertionError, TypeParamRef, make_union,
)

# Stdlib path needed for from_source when code uses primitive type methods
_STDLIB_DIRS = [get_lib_dir() / "tpy"]


class TestCompilerFromSource:
    def test_simple_program(self):
        source = 'print("hello")'
        compiler = Compiler.from_source(source)
        modules = compiler.compile()
        assert len(modules) == 1
        assert modules[0].name == "main"
        assert modules[0].is_entry_point

    def test_dependency_own_copy_verdict_discharged(self, tmp_path):
        """`from_source` must run the same workspace-wide passes as the file
        path. A generic defined in a dependency and instantiated by the entry
        module at a non-copyable type is rejected in the dependency's body;
        when this path skipped the discharge, the placeholder was withdrawn
        instead and the copy came back as a C++ build failure.
        """
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "slots.py").write_text(
            "from tpy import Int32\n"
            "\n"
            "\n"
            "def insert_slot[T](v: T) -> Int32:\n"
            "    xs: list[T] = []\n"
            "    xs.append(v)\n"
            "    return len(xs)\n"
        )
        # The slot's source must be a NAME: an rvalue constructs in place and
        # copies nothing, so it would report nothing on either path.
        source = (
            "from tpy import Int32\n"
            "from slots import insert_slot\n"
            "\n"
            "\n"
            "class Pinned:\n"
            "    n: Int32\n"
            "\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "\n"
            # `__del__` is what deletes the generated copy ctor, which is the
            # leg that must error. `@nomove` would forbid only the MOVE.
            "    def __del__(self) -> None:\n"
            "        self.n = 0\n"
            "\n"
            "\n"
            "def main() -> None:\n"
            "    p = Pinned(3)\n"
            "    print(insert_slot(p))\n"
            "\n"
            "\n"
            "main()\n"
        )
        compiler = Compiler.from_source(
            source, lib_dirs=[lib] + _STDLIB_DIRS)
        modules = compiler.compile()
        errors = [
            (m.name, d)
            for m in modules
            if m.analyzer is not None
            for d in m.analyzer.diagnostics
            if d.level == DiagnosticLevel.ERROR
        ]
        # The verdict is reported at the copying line in the DEPENDENCY's body
        # -- `xs.append(v)`, line 6 of slots.py -- and not at the instantiating
        # call in the entry module, on either compile path. The owning module
        # supplies the file name (`loc.file` stays None for a location in the
        # module being analyzed; `Diagnostic.format` takes it from the caller),
        # so the module name is asserted alongside the line.
        located = [
            (mod, d.loc.file, d.loc.line) for mod, d in errors
            if "cannot copy non-copyable type 'Pinned' into owned storage"
            in d.message and d.loc is not None
        ]
        assert located == [("slots", None, 6)], [
            (mod, d.message[:60], d.loc) for mod, d in errors]

    def test_two_noncopyable_instantiations_report_both(self, tmp_path):
        """One body, two non-copyable instantiations, two located errors on
        the SAME line -- and the two ways to be non-copyable, `@nocopy` and a
        `__del__`, side by side. The promotion writes the first verdict over
        the hedge and inserts a sibling for each further distinct one, so a
        body copied at two different non-copyable types names both rather than
        the first. No case can pin this: compilation stops at the first error,
        so an `error_` case would only ever show one.
        """
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "slots.py").write_text(
            "from tpy import Int32\n"
            "\n"
            "\n"
            "def insert_slot[T](v: T) -> Int32:\n"
            "    xs: list[T] = []\n"
            "    xs.append(v)\n"
            "    return len(xs)\n"
        )
        source = (
            "from tpy import Int32, nocopy\n"
            "from slots import insert_slot\n"
            "\n"
            "\n"
            "@nocopy\n"
            "class DeclaredA:\n"
            "    n: Int32\n"
            "\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "\n"
            "\n"
            "class DestructorB:\n"
            "    n: Int32\n"
            "\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "\n"
            "    def __del__(self) -> None:\n"
            "        self.n = 0\n"
            "\n"
            "\n"
            "def main() -> None:\n"
            "    a = DeclaredA(1)\n"
            "    b = DestructorB(2)\n"
            "    print(insert_slot(a), insert_slot(b))\n"
            "\n"
            "\n"
            "main()\n"
        )
        compiler = Compiler.from_source(
            source, lib_dirs=[lib] + _STDLIB_DIRS)
        modules = compiler.compile()
        located = sorted(
            (m.name, d.loc.line, d.message.split("'")[1])
            for m in modules
            if m.analyzer is not None
            for d in m.analyzer.diagnostics
            if d.level == DiagnosticLevel.ERROR and d.loc is not None
            and "cannot copy non-copyable type" in d.message
        )
        assert located == [
            ("slots", 6, "DeclaredA"), ("slots", 6, "DestructorB")], located

    def test_value_bound_silencer_reaches_any_nesting_depth(self, tmp_path):
        """The `T: ValueType` silencer walks the payload to the first
        reference-typed nominal at ANY depth, not just the outer one. Three
        payloads under the same bound: `GBox[T] | None` still hedges (the
        reference nominal is one level in, under the optional), while
        `tuple[T, Int32] | None` and `tuple[T, tuple[T, Int32]]` stay silent
        (delegating all the way down, so no struct is ever copied).

        No snapshot case can pin these: every owning sink for a nested
        payload is a THIR lowering reject today -- `method.arg_shape` at
        `.append`, `call.generic_arg_slot` at an `Own[T]` argument,
        `return.slot_type` / `return.tuple_source` at a return,
        `foreach.elem_family.optional` at a `yield` consumer and
        `call.ctor_arg.optional` at a field -- so a case would fail to
        compile rather than report.
        """
        source = (
            "from tpy import Int32, ValueType\n"
            "\n"
            "\n"
            "class GBox[T]:\n"
            "    item: T\n"
            "\n"
            "    def __init__(self, v: T) -> None:\n"
            "        self.item = v\n"
            "\n"
            "\n"
            "def nested_ref[T: ValueType](v: GBox[T] | None) -> Int32:\n"
            "    xs: list[GBox[T] | None] = []\n"
            "    xs.append(v)  # ref\n"
            "    return len(xs)\n"
            "\n"
            "\n"
            "def nested_val_opt[T: ValueType](v: tuple[T, Int32] | None) -> Int32:\n"
            "    xs: list[tuple[T, Int32] | None] = []\n"
            "    xs.append(v)  # val-opt\n"
            "    return len(xs)\n"
            "\n"
            "\n"
            "def nested_val_tuple[T: ValueType](v: tuple[T, tuple[T, Int32]]) -> Int32:\n"
            "    xs: list[tuple[T, tuple[T, Int32]]] = []\n"
            "    xs.append(v)  # val-tuple\n"
            "    return len(xs)\n"
            "\n"
            "\n"
            "def main() -> None:\n"
            "    print(nested_ref(GBox(1)), nested_val_opt((2, 3)),\n"
            "          nested_val_tuple((4, (5, 6))))\n"
            "\n"
            "\n"
            "main()\n"
        )
        lines = source.splitlines()
        ref_line = lines.index("    xs.append(v)  # ref") + 1
        compiler = Compiler.from_source(source, lib_dirs=list(_STDLIB_DIRS))
        modules = compiler.compile()
        slots = [
            (d.loc.line, d.message)
            for m in modules
            if m.analyzer is not None
            for d in m.analyzer.diagnostics
            if d.level == DiagnosticLevel.WARNING and d.loc is not None
            and "into owned storage" in d.message
        ]
        assert slots == [(
            ref_line,
            "may copy GBox[T] | None into owned storage if not a value type;"
            " use copy() to make this explicit",
        )], slots

    def test_custom_module_name(self):
        source = 'x: int = 1\nprint(x)'
        compiler = Compiler.from_source(source, module_name="test_mod")
        modules = compiler.compile()
        assert modules[0].name == "test_mod"

    def test_matches_file_compilation(self, tmp_path):
        """Verify that from_source and file-based compilation produce the same AST structure."""
        source = 'def add(a: int, b: int) -> int:\n    return a + b\nprint(add(1, 2))'
        src_file = tmp_path / "check.py"
        src_file.write_text(source)

        file_compiler = Compiler(src_file, lib_dirs=_STDLIB_DIRS)
        file_modules = file_compiler.compile()

        source_compiler = Compiler.from_source(source, lib_dirs=_STDLIB_DIRS)
        source_modules = source_compiler.compile()

        assert len(file_modules) == len(source_modules)
        file_ast = file_modules[0].ast
        source_ast = source_modules[0].ast
        assert len(file_ast.functions) == len(source_ast.functions)
        assert len(file_ast.top_level_stmts) == len(source_ast.top_level_stmts)

    @pytest.mark.parametrize(
        ("default_int", "expected"),
        [
            ("Int32", INT32),
            ("Int64", INT64),
            ("BigInt", BIGINT),
        ],
    )
    def test_default_int_setting_controls_unannotated_literals(self, default_int, expected):
        source = "x = 1\n"
        compiler = Compiler.from_source(source, default_int=default_int)
        modules = compiler.compile()
        hpp, _ = compiler.generate_code_to_strings(modules[0])
        expected_cpp = expected.to_cpp()
        assert f"extern {expected_cpp} x;" in hpp

    def test_reassignment_from_bigint_widens_default_int(self):
        source = "x = 0\nx = int(5)\n"
        compiler = Compiler.from_source(source, default_int="Int32", lib_dirs=_STDLIB_DIRS)
        modules = compiler.compile()
        entry = [m for m in modules if m.is_entry_point][0]
        hpp, _ = compiler.generate_code_to_strings(entry)
        assert "extern ::tpy::BigInt x;" in hpp

    def test_invalid_default_int_setting_rejected(self):
        with pytest.raises(ValueError, match="Unsupported default int type"):
            Compiler.from_source("x = 1\n", default_int="Int128")

    def test_native_module_propagation(self):
        """builtins._list inherits native_module from builtins/__init__."""
        lib_dir = get_lib_dir() / "tpy"
        compiler = Compiler.from_source("x = [1]\n", lib_dirs=[lib_dir])
        compiler.compile()
        mod = compiler.modules.get("builtins._list")
        if mod is not None:
            assert mod.ast.directives.native_module
            _, cpp = compiler.generate_code_to_strings(mod)
            assert cpp == ""



class _CoroImportPlugin(FrontendPlugin):
    name = "coro_import"
    extensions = (".coro_import",)

    def parse(self, ctx: WorkspaceContext,
              module_name: str, file_path: Path) -> FrontendOutput:
        return FrontendOutput(module=FrontendModule(
            qname=module_name, source_language=self.name,
            imports=(Import(module="worker"),),
        ))


class TestImplicitCoroutineRuntime:
    @pytest.mark.parametrize("source", [
        "async def compute(n: int) -> int:\n    return n\n",
        "async def compute(n: tuple[int]) -> tuple[int]:\n    return n\n",
        "async def compute(n: int | None) -> int | None:\n    return n\n",
        "async def compute[T](n: T) -> T:\n    return n\n",
        "class Worker:\n    async def compute(self) -> None:\n        pass\n",
        "class Outer:\n    class Worker:\n"
        "        async def compute(self) -> None:\n            pass\n",
    ], ids=["scalar", "tuple", "optional", "generic", "method", "nested_record"])
    def test_file_and_source_discover_runtime(self, tmp_path: Path, source: str) -> None:
        src_file = tmp_path / "main.py"
        src_file.write_text(source)
        compilers = [
            Compiler(src_file, lib_dirs=_STDLIB_DIRS),
            Compiler.from_source(source, lib_dirs=_STDLIB_DIRS),
        ]
        emitted = []
        for compiler in compilers:
            modules = compiler.compile()
            entry = next(m for m in modules if m.is_entry_point)
            hpp, cpp = compiler.generate_code_to_strings(entry)
            assert "tpy.coro" in entry.ast.user_module_imports
            assert compiler.compile_order.index("tpy.coro") < compiler.compile_order.index("main")
            assert hpp.count('#include "tpystd/coro.hpp"') == 1
            assert "::tpystd::coro::Waker" in hpp
            assert not any(name == "asyncio" or name.startswith("asyncio.")
                           for name in compiler.modules)
            assert not entry.ast.imports
            assert not entry.ast.bare_module_imports
            assert not entry.ast.module_aliases
            assert not any(isinstance(stmt, TpyImport) for stmt in entry.ast.top_level_stmts)
            assert "::tpystd::coro::__tpy_init()" not in cpp
            assert "tpy.coro" not in compiler.modules["tpy.coro"].ast.user_module_imports
            emitted.append((hpp, cpp))
        assert compilers[0].modules.keys() == compilers[1].modules.keys()
        assert emitted[0] == emitted[1]

    def test_each_async_owner_gets_edge_when_runtime_already_discovered(self, tmp_path: Path) -> None:
        (tmp_path / "explicit.py").write_text("import tpy.coro\n")
        (tmp_path / "worker.py").write_text("async def compute() -> None:\n    pass\n")
        (tmp_path / "sync_neighbor.py").write_text("def compute() -> None:\n    pass\n")
        entry = tmp_path / "main.py"
        entry.write_text("import explicit\nimport worker\nimport sync_neighbor\n")
        compiler = Compiler(entry, lib_dirs=_STDLIB_DIRS)
        compiler.compile()
        worker = compiler.modules["worker"]
        hpp, cpp = compiler.generate_code_to_strings(worker)
        assert "tpy.coro" in worker.ast.user_module_imports
        assert hpp.count('#include "tpystd/coro.hpp"') == 1
        assert "::tpystd::coro::__tpy_init()" not in cpp
        for name in ("main", "sync_neighbor"):
            module = compiler.modules[name]
            assert "tpy.coro" not in module.ast.user_module_imports
        # Implicit stdlib prefix reach can add headers without declaration edges.
        neighbor = compiler.modules["sync_neighbor"]
        assert not neighbor.ast.imports
        assert not neighbor.ast.bare_module_imports
        assert not neighbor.ast.module_aliases
        assert not any(isinstance(stmt, TpyImport) for stmt in neighbor.ast.top_level_stmts)
        _, cpp = compiler.generate_code_to_strings(neighbor)
        assert "::tpystd::coro::__tpy_init()" not in cpp

    def test_explicit_import_is_idempotent(self) -> None:
        source = "import tpy.coro\nasync def compute() -> None:\n    pass\n"
        compiler = Compiler.from_source(source, lib_dirs=_STDLIB_DIRS)
        modules = compiler.compile()
        entry = next(m for m in modules if m.is_entry_point)
        hpp, _ = compiler.generate_code_to_strings(entry)
        assert sum(m.name == "tpy.coro" for m in modules) == 1
        assert hpp.count('#include "tpystd/coro.hpp"') == 1
        assert sum(isinstance(stmt, TpyImport) and stmt.module_name == "tpy.coro"
                   for stmt in entry.ast.top_level_stmts) == 1

    @pytest.mark.parametrize("source", [
        "def compute() -> None:\n    pass\n",
        "from typing import Iterator\ndef values() -> Iterator[int]:\n    yield 1\n",
    ], ids=["sync", "generator"])
    def test_sync_and_generator_do_not_load_runtime(self, source: str) -> None:
        compiler = Compiler.from_source(source, lib_dirs=_STDLIB_DIRS)
        modules = compiler.compile()
        entry = next(m for m in modules if m.is_entry_point)
        hpp, _ = compiler.generate_code_to_strings(entry)
        assert "tpy.coro" not in compiler.modules
        assert "tpy.coro" not in entry.ast.user_module_imports
        assert '#include "tpystd/coro.hpp"' not in hpp

    def test_unused_inline_async_conservatively_loads_runtime(self) -> None:
        source = (
            "from tpy import inline\n@inline\n"
            "async def compute(n: int) -> None:\n    print(n)\n"
        )
        compiler = Compiler.from_source(source, lib_dirs=_STDLIB_DIRS)
        modules = compiler.compile()
        entry = next(m for m in modules if m.is_entry_point)
        hpp, _ = compiler.generate_code_to_strings(entry)
        assert "tpy.coro" in compiler.modules
        assert "tpy.coro" in entry.ast.user_module_imports
        assert '#include "tpystd/coro.hpp"' in hpp
        assert "::tpystd::coro::Waker" not in hpp
        assert not any(name == "asyncio" or name.startswith("asyncio.")
                       for name in compiler.modules)

    @pytest.mark.parametrize("route", ["file", "source", "empty_roots"])
    def test_missing_runtime_reports_first_async_location(self, tmp_path: Path, route: str) -> None:
        source = (
            "class Outer:\n    class Worker:\n"
            "        async def compute(self) -> None:\n            pass\n\n"
            "async def later() -> None:\n    pass\n"
        )
        src_file = tmp_path / "main.py"
        src_file.write_text(source)
        if route == "file":
            compiler = Compiler(src_file)
        else:
            compiler = Compiler.from_source(
                source, lib_dirs=[tmp_path] if route == "empty_roots" else None,
            )
        with pytest.raises(CompileError, match="coroutine runtime 'tpy.coro'") as exc_info:
            compiler.compile()
        error = exc_info.value
        assert error.module_name == "main"
        assert error.lineno == 3
        assert error.path == (src_file if route == "file" else Path("<stdin>"))
        assert "enable the standard library" in error.message
        assert "library paths" in error.message
        assert not any(token in error.message for token in ("Waker", ".hpp", "::", "Tpy"))
        assert "tpy.coro" not in compiler.modules

    def test_configured_library_root_supplies_runtime(self, tmp_path: Path) -> None:
        # An alternate configured root must work without the installed root in lib_dirs.
        library_root = tmp_path / "custom_library"
        library_root.symlink_to(_STDLIB_DIRS[0], target_is_directory=True)
        compiler = Compiler.from_source(
            "async def compute() -> None:\n    pass\n", lib_dirs=[library_root],
        )
        modules = compiler.compile()
        entry = next(m for m in modules if m.is_entry_point)
        hpp, _ = compiler.generate_code_to_strings(entry)
        assert "tpy.coro" in entry.ast.user_module_imports
        assert '#include "tpystd/coro.hpp"' in hpp
        runtime = compiler.modules["tpy.coro"]
        configured_runtime = library_root / "tpy/coro/__init__.py"
        assert runtime.path == configured_runtime
        assert compiler.input_file_entries[str(configured_runtime)]["path"] == str(configured_runtime)

    def test_plugin_imported_python_async_gets_runtime(self, tmp_path: Path) -> None:
        (tmp_path / "worker.py").write_text("async def compute() -> None:\n    pass\n")
        entry = tmp_path / "main.coro_import"
        entry.write_text("")
        registry = FrontendRegistry()
        registry.register(_CoroImportPlugin({}))
        compiler = Compiler(entry, lib_dirs=_STDLIB_DIRS, frontend_registry=registry)
        compiler.compile()
        worker = compiler.modules["worker"]
        hpp, _ = compiler.generate_code_to_strings(worker)
        assert "tpy.coro" in worker.ast.user_module_imports
        assert '#include "tpystd/coro.hpp"' in hpp
        assert "tpy.coro" not in compiler.modules["main"].ast.user_module_imports


class TestThirRouting:
    """THIR is the author for EVERY module -- user code, lib/tpy and the
    stdlib alike. That is a routing DECISION the snapshot compare cannot see
    on its own: a module that stopped lowering would still have to emit
    something, so the routing claim needs its own assertion."""

    def test_every_module_routes_thir_by_default(self, tmp_path):
        src_file = tmp_path / "main.py"
        src_file.write_text("def f(x: int) -> int:\n    return x + 1\n\nprint(f(1))\n")
        compiler = Compiler(src_file, lib_dirs=_STDLIB_DIRS)
        modules = compiler.compile()
        entry = next(m for m in modules if m.is_entry_point)
        for m in modules:
            compiler.generate_code(m, tmp_path / "out",
                                   entry_module_name=entry.name)
        # Compared against an INDEPENDENTLY derived candidate set rather than
        # against "at least one body somewhere": a module whose bodies stopped
        # being THIR's would still emit, and a per-module count > 0 is silent
        # about the ones that did not.
        routed = compiler._thir_routed_names
        expected = {}
        for m in modules:
            plain = [fn.name for fn, _self
                     in iter_module_callables(m.ast, m.analyzer)
                     # Resumable and simple-generator bodies lower at their
                     # frame seams and key their own caches, so they are not
                     # in this map; a bodyless binding has nothing to lower.
                     if not (is_bodyless_binding(fn) or fn.is_async
                             or fn.is_generator)]
            if plain:
                expected[m.name] = plain
        assert entry.name in expected, "fixture entry defines no plain callable"
        non_user = [m.name for m in modules if not compiler.is_user_module(m)]
        assert any(name in expected for name in non_user), (
            "fixture compiled no library module with a body -- test is vacuous")
        missing = sorted(name for name in expected if not routed.get(name))
        assert not missing, (
            f"modules with bodies but no THIR routing: {missing} -- their "
            "bodies were emitted by something other than THIR")

    def test_real_codegen_closes_the_witness_journal(self, tmp_path):
        """Codegen leaves no journal open, so a witness recorded after the last
        attempt (emit-time ones especially) is not rollback-eligible.

        SCOPE, measured rather than assumed: this catches `commit_attempt()`
        being unwired ENTIRELY, not one branch of six dropping it -- with a
        single site removed the assertion still passes, because a later
        attempt's `begin_attempt` resets the journal anyway. That is also why
        a missing site is harmless today (see `faces.commit_witnesses`). The
        rollback SEMANTICS are pinned in `tpyc/thir/test_faces.py`.

        Deliberately does NOT pair a routed body with a falling-back one: the
        blocking construct would have to keep falling back to stay meaningful,
        and the whole active workstream is making such constructs route."""
        src_file = tmp_path / "main.py"
        src_file.write_text("def f(x: int) -> int:\n    return x + 1\n\nprint(f(1))\n")
        compiler = Compiler(src_file, lib_dirs=_STDLIB_DIRS)
        modules = compiler.compile()
        entry = next(m for m in modules if m.is_entry_point)
        for m in modules:
            compiler.generate_code(m, tmp_path / "out",
                                   entry_module_name=entry.name,
                                   )
        assert compiler._thir_face_witnesses, "no face witnessed -- test is vacuous"
        assert compiler._thir_face_journal is None, (
            "witness journal left OPEN after codegen -- a routed branch is "
            "missing its commit_attempt(), so these witnesses are exposed to "
            "the next attempt's rollback")

    def test_clean_body_lowers_every_user_callable(self, tmp_path):
        """A fully-lowerable user body leaves nothing on a second author: every
        callable in the module has a THIR entry after codegen, which is what
        makes the emitted C++ THIR's rather than something the skeleton
        improvised."""
        src_file = tmp_path / "main.py"
        src_file.write_text("def f(x: int) -> int:\n    return x + 1\n\nprint(f(1))\n")
        compiler = Compiler(src_file, lib_dirs=_STDLIB_DIRS)
        modules = compiler.compile()
        entry = next(m for m in modules if m.is_entry_point)
        for m in modules:
            compiler.generate_code(m, tmp_path / "out",
                                   entry_module_name=entry.name)
        assert compiler._thir_routed_names.get(entry.name) == frozenset({"f"})


class TestCodegenStateIsolation:
    """Per-module codegen state (`clear_codegen_state`) must not leak across
    module passes. The AST pipeline emits in topological order, so a definer
    never follows its importer -- the leak stays invisible there and only
    surfaces when a module is emitted twice (the THIR overlay's re-emit)."""

    _TREELIB = "type Tree[T] = T | list[Tree[T]]\n"
    _MAIN = (
        "from treelib import Tree\n\n\n"
        "def count(t: Tree[int]) -> int:\n"
        "    return 1\n"
    )
    # `mid` is both a definer (its own `Tree`) and an import target (`main`
    # imports `mid.Tree`), so a leaked entry hits it while the imported
    # `base.Tree` must stay qualified -- both colliding qnames in one re-emit.
    _MID = (
        "import base\n\n"
        "type Tree[T] = T | list[Tree[T]]\n\n\n"
        "def count(mine: Tree[int], theirs: base.Tree[int]) -> int:\n"
        "    return 1\n"
    )
    _MAIN_VIA_MID = (
        "from mid import Tree\n\n\n"
        "def go(t: Tree[int]) -> int:\n"
        "    return 1\n"
    )

    def _compile(self, tmp_path):
        (tmp_path / "treelib.py").write_text(self._TREELIB)
        src_file = tmp_path / "main.py"
        src_file.write_text(self._MAIN)
        compiler = Compiler(src_file, lib_dirs=_STDLIB_DIRS)
        return compiler, compiler.compile()

    def _compile_chain(self, tmp_path):
        (tmp_path / "base.py").write_text(self._TREELIB)
        (tmp_path / "mid.py").write_text(self._MID)
        src_file = tmp_path / "main.py"
        src_file.write_text(self._MAIN_VIA_MID)
        compiler = Compiler(src_file, lib_dirs=_STDLIB_DIRS)
        return compiler, compiler.compile()

    def test_definer_reemit_after_importer_is_stable(self, tmp_path):
        """A generic recursive alias renders bare in its DEFINING module. Only
        importers register it in `recursive_alias_cpp_names`, so a stale entry
        would make the definer qualify against itself on a second pass."""
        compiler, modules = self._compile(tmp_path)
        treelib = next(m for m in modules if m.name == "treelib")

        first, _ = compiler.generate_code_to_strings(treelib)
        for mod in modules:  # importer's pass registers `treelib.Tree`
            compiler.generate_code_to_strings(mod)
        second, _ = compiler.generate_code_to_strings(treelib)

        assert first == second, "definer re-emit diverged after importer's pass"
        assert "::tpyapp::treelib::Tree" not in second

    def test_importer_still_qualifies(self, tmp_path):
        """The inverse: clearing must not under-register -- an importing module
        still spells the imported alias with its defining module's namespace."""
        compiler, modules = self._compile(tmp_path)
        entry = next(m for m in modules if m.is_entry_point)
        hpp, cpp = compiler.generate_code_to_strings(entry)
        assert "::tpyapp::treelib::Tree" in hpp + cpp

    def test_colliding_aliases_stay_distinct_across_passes(self, tmp_path):
        """Two same-short-named `Tree`s meeting in one module's re-emit: `mid`
        renders its own bare and `base`'s qualified. `main` imports `mid.Tree`,
        so mid's own qname is the one a leak would strand in the map."""
        compiler, modules = self._compile_chain(tmp_path)
        mid = next(m for m in modules if m.name == "mid")

        first, _ = compiler.generate_code_to_strings(mid)
        for mod in modules:  # main's pass registers `mid.Tree`
            compiler.generate_code_to_strings(mod)
        second, _ = compiler.generate_code_to_strings(mid)

        assert first == second, "collision re-emit diverged after import registration"
        assert "::tpyapp::base::Tree" in second  # the imported one stays qualified
        assert "::tpyapp::mid::Tree" not in second  # the local one stays bare


class TestCodegenRegression:
    def test_generic_ctor_invalid_arg_rejected(self, tmp_path):
        """Invalid generic constructor arg (Int32 for Span[T] param) must be rejected by sema."""
        source = (
            'from tpy import Span, Int32\n'
            'x: Span[Int32] = Span[Int32](Int32(1))\n'
        )
        src_file = tmp_path / "test.py"
        src_file.write_text(source)
        compiler = Compiler(src_file, lib_dirs=[get_lib_dir() / "tpy"])
        with pytest.raises(SemanticError, match="cannot be constructed from"):
            compiler.compile()


class TestSendSync:
    """Test Send/Sync auto-derivation on built-in types."""

    # -- Primitive value types: all Send + Sync --

    @pytest.mark.parametrize("typ", [INT32, INT64, BIGINT, BOOL, FLOAT, STR, CHAR])
    def test_primitives_are_send_and_sync(self, typ):
        assert typ.is_send()
        assert typ.is_sync()

    # -- StrView: borrows, not Send, but Sync (read-only) --

    def test_strview_not_send(self):
        assert not STRVIEW.is_send()

    def test_strview_is_sync(self):
        assert STRVIEW.is_sync()

    # -- Ptr: not Send; Sync only if readonly --

    def test_ptr_not_send(self):
        assert not PtrType(INT32).is_send()
        assert not PtrType(INT32, is_readonly=True).is_send()

    def test_mutable_ptr_not_sync(self):
        assert not PtrType(INT32).is_sync()

    def test_readonly_ptr_sync_if_pointee_sync(self):
        assert PtrType(INT32, is_readonly=True).is_sync()
        # Ptr[readonly[list[T]]]: list is not Sync
        assert not PtrType(make_list(INT32), is_readonly=True).is_sync()

    # -- Span: not Send; Sync only if readonly --

    def test_span_not_send(self):
        assert not make_span(INT32).is_send()
        assert not make_span(INT32, is_readonly=True).is_send()

    def test_mutable_span_not_sync(self):
        assert not make_span(INT32).is_sync()

    def test_readonly_span_sync_if_element_sync(self):
        assert make_span(INT32, is_readonly=True).is_sync()

    # -- list: Send if element Send, never Sync --

    def test_list_send_if_element_send(self):
        assert make_list(INT32).is_send()
        assert not make_list(PtrType(INT32)).is_send()

    def test_list_not_sync(self):
        assert not make_list(INT32).is_sync()

    # -- dict: Send if elements Send, never Sync --

    def test_dict_send_if_elements_send(self):
        assert make_dict(STR, INT32).is_send()
        assert not make_dict(STR, PtrType(INT32)).is_send()

    def test_dict_not_sync(self):
        assert not make_dict(STR, INT32).is_sync()

    # -- Array: Send/Sync based on element --

    def test_array_send_sync_based_on_element(self):
        assert make_array(INT32, 10).is_send()
        assert make_array(INT32, 10).is_sync()
        assert not make_array(PtrType(INT32), 10).is_send()

    # -- Tuple: Send/Sync if all elements are --

    def test_tuple_send_sync(self):
        assert TupleType((INT32, STR)).is_send()
        assert TupleType((INT32, STR)).is_sync()
        assert not TupleType((INT32, PtrType(STR))).is_send()

    # -- Optional: delegates to inner --

    def test_optional_delegates(self):
        assert OptionalType(INT32).is_send()
        # OptionalType inner = NominalType (non-Send record) -- delegates through.
        # We can't use OptionalType(PtrType(...)) here because the __new__ collapse
        # would return a bare PtrType (Ptr[T] is already nullable), so the test
        # would exercise PtrType, not OptionalType's delegation.
        record = NominalType("R", (), _module_qname="__main__.R")
        assert not OptionalType(record).is_send()

    # -- Union: all members --

    def test_union_all_members(self):
        assert UnionType((INT32, STR)).is_send()
        assert not UnionType((INT32, PtrType(STR))).is_send()

    # -- Own: delegates to wrapped --

    def test_own_send_delegates_sync_false(self):
        assert OwnType(make_list(INT32)).is_send()
        # Own[T] is never Sync: single-owner move slot, sharing readonly
        # aliases of it is a category error regardless of T.
        assert not OwnType(make_array(INT32, 4)).is_sync()

    # -- readonly: borrow-side restriction only, no Send->Sync lift --

    def test_readonly_no_send_to_sync_lift(self):
        # The object may be mutated through other non-readonly aliases,
        # so readonly[list[Int32]] is NOT Sync even though list is Send.
        assert not make_list(INT32).is_sync()
        assert not ReadonlyType(make_list(INT32)).is_sync()

    def test_readonly_of_sync_stays_sync(self):
        rop = PtrType(INT32, is_readonly=True)
        assert not rop.is_send()
        assert rop.is_sync()
        assert ReadonlyType(rop).is_sync()

    def test_readonly_of_non_send_not_sync(self):
        # readonly[list[Ptr[T]]]: Ptr not Send, so list not Send, not Sync
        assert not ReadonlyType(make_list(PtrType(INT32))).is_sync()

    # -- Callable: erased closure may capture non-Send state --

    def test_callable_not_send_not_sync(self):
        cb = CallableType((INT32,), VOID)
        assert not cb.is_send()
        assert not cb.is_sync()

    # -- bytearray: mutable buffer, same Sync rule as list --

    def test_bytearray_send_not_sync(self):
        assert BYTEARRAY.is_send()
        assert not BYTEARRAY.is_sync()

    # -- frame slots: a tuple with reference elements lowers to a borrow-
    # pointer field (std::tuple<T*, ...>), so its slot is non-Send even when
    # the tuple's storage-form is_send() is True. (list[Int32] is the
    # registry-free stand-in for a Send-but-non-value element.)

    def test_frame_slot_borrow_tuple_not_send(self):
        from .sema.frame_traits import param_slot
        borrow = TupleType((make_list(INT32), make_list(INT32)))
        assert borrow.is_send()  # storage-form says Send...
        s = param_slot("p", borrow)
        assert not s.send and not s.sync  # ...but the borrow slot is not

    def test_frame_slot_readonly_borrow_tuple_not_send(self):
        from .sema.frame_traits import param_slot
        s = param_slot("p", ReadonlyType(TupleType((make_list(INT32), make_list(INT32)))))
        assert not s.send and not s.sync  # readonly wrapper must be seen through

    def test_frame_slot_value_tuple_stays_send(self):
        from .sema.frame_traits import param_slot
        s = param_slot("p", TupleType((INT32, INT32)))
        assert s.send and s.sync  # value elements -> owned storage, Send

    def test_frame_slot_union_element_tuple_conservative(self):
        # A union-element tuple is actually stored OWNED by codegen
        # (std::tuple<std::variant<A, B>, ...>), so it could be Send. We
        # classify it non-Send anyway: _value_slot_traits gates on the wider
        # has_ref_elements() rather than codegen's has_pointer_repr_element()
        # (which excludes unions). This pins the intentional over-conservatism
        # -- do NOT "align with codegen" by tightening here without the shared
        # storage-form predicate (see TODO.md), or you risk a false-Send on a
        # shape that genuinely does borrow.
        from .sema.frame_traits import param_slot
        s = param_slot("p", TupleType((UnionType((make_list(INT32), STR)), INT32)))
        assert not s.send and not s.sync


class TestSendSyncMarkerCanonicalization:
    """make_send_marker / make_sync_marker canonicalization rules."""

    def _cb(self):
        return CallableType((INT32,), VOID)

    # -- non-erased types: static assertion, wrapper erases --

    def test_send_of_send_type_erases(self):
        assert make_send_marker(INT32) == INT32
        assert make_send_marker(make_list(INT32)) == make_list(INT32)

    def test_sync_of_sync_type_erases(self):
        assert make_sync_marker(INT32) == INT32

    def test_assertion_failure_raises(self):
        with pytest.raises(MarkerAssertionError):
            make_send_marker(PtrType(INT32))
        with pytest.raises(MarkerAssertionError):
            make_sync_marker(make_list(INT32))

    def test_lenient_keeps_wrapper(self):
        m = make_send_marker(PtrType(INT32), lenient=True)
        assert isinstance(m, SendType)

    # -- erased types (Callable): wrapper persists --

    def test_callable_persists(self):
        m = make_send_marker(self._cb())
        assert isinstance(m, SendType)
        assert m.is_send() and not m.is_sync()
        s = make_sync_marker(self._cb())
        assert isinstance(s, SyncType)
        assert s.is_sync() and not s.is_send()

    def test_idempotent(self):
        m = make_send_marker(make_send_marker(self._cb()))
        assert isinstance(m, SendType)
        assert not isinstance(m.wrapped, SendType)

    def test_send_floats_outside_sync(self):
        # Sync[Send[T]] and Send[Sync[T]] both canonicalize to Send[Sync[T]]
        a = make_sync_marker(make_send_marker(self._cb()))
        b = make_send_marker(make_sync_marker(self._cb()))
        assert a == b
        assert isinstance(a, SendType) and isinstance(a.wrapped, SyncType)
        assert a.is_send() and a.is_sync()

    def test_distributes_over_optional(self):
        m = make_send_marker(OptionalType(self._cb()))
        assert isinstance(m, OptionalType)
        assert isinstance(m.inner, SendType)

    def test_erases_inside_union_of_send_types(self):
        u = make_union(INT32, STR)
        assert make_send_marker(u) == u

    def test_fn_template_rejected(self):
        with pytest.raises(MarkerAssertionError):
            make_send_marker(make_fn_type((INT32,), VOID))

    def test_cpp_representation_identical(self):
        cb = self._cb()
        m = make_send_marker(cb)
        assert m.to_cpp() == cb.to_cpp()
        assert m.to_cpp_param_type() == cb.to_cpp_param_type()
        assert m.is_value_type() == cb.is_value_type()

    def test_substitution_recanonicalizes(self):
        # Send[T] substituted with a concrete Send type erases the wrapper
        m = SendType(TypeParamRef("T"))
        assert m.with_inner_types((INT32,)) == INT32
        # ...and keeps it (lenient) for a non-Send concrete type
        kept = m.with_inner_types((PtrType(INT32),))
        assert isinstance(kept, SendType)


class TestSendSyncRecordDerivation:
    """Test Send/Sync auto-derivation on user-defined records."""

    def test_record_with_value_fields_is_send_sync(self):
        source = (
            "from tpy import Int32\n"
            "class Point:\n"
            "    x: Int32\n"
            "    y: Int32\n"
            "    def __init__(self, x: Int32, y: Int32) -> None:\n"
            "        self.x = x\n"
            "        self.y = y\n"
            "def main() -> None:\n"
            "    p = Point(Int32(1), Int32(2))\n"
            "    print(p.x)\n"
            "main()\n"
        )
        compiler = Compiler.from_source(source, lib_dirs=_STDLIB_DIRS)
        compiler.compile()
        point_type = NominalType("Point", (), _module_qname="__main__.Point")
        with activate_compiler(compiler):
            assert point_type.is_send()
            assert point_type.is_sync()

    def test_record_with_ptr_field_not_send(self, tmp_path):
        source = (
            "from tpy import Ptr, Int32, take_ptr\n"
            "class Wrapper:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "class Holder:\n"
            "    p: Ptr[Wrapper]\n"
            "    def __init__(self, p: Ptr[Wrapper]) -> None:\n"
            "        self.p = p\n"
            "def main() -> None:\n"
            "    w = Wrapper(Int32(1))\n"
            "    h = Holder(take_ptr(w))\n"
            "    print(h.p.x)\n"
            "main()\n"
        )
        src_file = tmp_path / "test.py"
        src_file.write_text(source)
        compiler = Compiler(src_file, lib_dirs=[get_lib_dir() / "tpy"])
        compiler.compile()
        holder_type = NominalType("Holder", (), _module_qname="__main__.Holder")
        with activate_compiler(compiler):
            assert not holder_type.is_send()
            assert not holder_type.is_sync()

    def test_record_with_list_field_send_not_sync(self):
        source = (
            "from tpy import Int32\n"
            "class Container:\n"
            "    items: list[Int32]\n"
            "    def __init__(self) -> None:\n"
            "        self.items = [Int32(1)]\n"
            "def main() -> None:\n"
            "    c = Container()\n"
            "    print(len(c.items))\n"
            "main()\n"
        )
        from . import get_lib_dir
        lib_dirs = [get_lib_dir() / "tpy"]
        compiler = Compiler.from_source(source, lib_dirs=lib_dirs)
        compiler.compile()
        container_type = NominalType("Container", (), _module_qname="__main__.Container")
        with activate_compiler(compiler):
            assert container_type.is_send()
            assert not container_type.is_sync()


class TestBuildLayout:
    def setup_method(self):
        self.layout = BuildLayout(Path("/out"), "main")

    def test_simple_module_hpp(self):
        assert self.layout.hpp_path("main") == Path("/out/main.d/include/main.hpp")

    def test_simple_module_cpp(self):
        assert self.layout.cpp_path("main") == Path("/out/main.d/src/main.cpp")

    def test_dotted_module_hpp(self):
        assert self.layout.hpp_path("pkg.mod") == Path("/out/main.d/include/pkg/mod.hpp")

    def test_dotted_module_cpp(self):
        assert self.layout.cpp_path("pkg.mod") == Path("/out/main.d/src/pkg/mod.cpp")

    def test_deeply_nested_hpp(self):
        assert self.layout.hpp_path("a.b.c.d") == Path("/out/main.d/include/a/b/c/d.hpp")

    def test_deeply_nested_cpp(self):
        assert self.layout.cpp_path("a.b.c.d") == Path("/out/main.d/src/a/b/c/d.cpp")

    def test_binary_path(self):
        assert self.layout.binary_path() == Path("/out/main.d/main")

    def test_binary_path_with_variant(self):
        layout = BuildLayout(Path("/out"), "main", build_variant="debug")
        assert layout.binary_path() == Path("/out/main.d/debug/main")

    def test_binary_path_release_variant(self):
        layout = BuildLayout(Path("/out"), "main", build_variant="release")
        assert layout.binary_path() == Path("/out/main.d/release/main")

    def test_build_dir_no_variant(self):
        assert self.layout.build_dir == Path("/out/main.d")

    def test_build_dir_with_variant(self):
        layout = BuildLayout(Path("/out"), "main", build_variant="debug")
        assert layout.build_dir == Path("/out/main.d/debug")

    def test_darwin_cross_ld_path_on_link_only(self, tmp_path: Path):
        """A darwin-cross toolchain's --ld-path lands on the link step only:
        it errors as unused on a -c compile, so it must not appear there."""
        from .compiler import CppCompilerConfig

        triple = "arm64-apple-darwin25.5"
        cxx = tmp_path / f"{triple}-clang++-19"
        cxx.write_text(f"#!/bin/sh\necho {triple}\n")
        cxx.chmod(0o755)
        ld = tmp_path / f"{triple}-ld"
        ld.write_text("#!/bin/sh\n")
        ld.chmod(0o755)

        layout = BuildLayout(tmp_path / "b", "app")
        layout.src_dir.mkdir(parents=True)
        cpp = layout.src_dir / "app.cpp"
        cpp.write_text("// generated")
        cmds = layout.build_cpp_commands(
            runtime_include_dir=tmp_path / "rt",
            cpp_files=[cpp],
            config=CppCompilerConfig(compiler=[str(cxx)]),
        )
        flag = f"--ld-path={ld}"
        *compiles, link = cmds
        assert flag in link
        assert all(flag not in c for c in compiles)


class TestGenerateCmake:
    """Tests for sources.cmake generation and runtime bundling."""

    def _make_layout(self, tmp_path: Path) -> BuildLayout:
        layout = BuildLayout(tmp_path, "app")
        layout.include_dir.mkdir(parents=True)
        layout.src_dir.mkdir(parents=True)
        return layout

    def _dummy_cpp(self, layout: BuildLayout) -> list[Path]:
        cpp = layout.src_dir / "app.cpp"
        cpp.write_text("// generated")
        return [cpp]

    def test_bundle_runtime_copies_headers(self, tmp_path: Path):
        layout = self._make_layout(tmp_path)
        runtime_inc = get_runtime_dir() / "cpp" / "include"
        layout.generate_cmake(
            runtime_include_dir=runtime_inc,
            cpp_files=self._dummy_cpp(layout),
            bundle_runtime=True,
        )
        bundled = layout.root_dir / "runtime" / "include" / "tpy"
        assert bundled.is_dir()
        assert (bundled / "tpy.hpp").is_file()

    def test_bundle_runtime_cmake_uses_local_path(self, tmp_path: Path):
        layout = self._make_layout(tmp_path)
        runtime_inc = get_runtime_dir() / "cpp" / "include"
        cmake_path = layout.generate_cmake(
            runtime_include_dir=runtime_inc,
            cpp_files=self._dummy_cpp(layout),
            bundle_runtime=True,
        )
        content = cmake_path.read_text()
        assert "${CMAKE_CURRENT_LIST_DIR}/runtime/include" in content
        # No absolute or parent-traversal paths to the runtime
        assert "../../" not in content.split("TPYC_INCLUDE_DIRS")[1].split(")")[0]

    def test_no_bundle_runtime_uses_relative_path(self, tmp_path: Path):
        layout = self._make_layout(tmp_path)
        runtime_inc = get_runtime_dir() / "cpp" / "include"
        cmake_path = layout.generate_cmake(
            runtime_include_dir=runtime_inc,
            cpp_files=self._dummy_cpp(layout),
            bundle_runtime=False,
        )
        content = cmake_path.read_text()
        bundled = layout.root_dir / "runtime" / "include"
        assert not bundled.exists()
        assert "runtime/include" not in content
        # Should reference the runtime via a relative or absolute path
        lines = content.splitlines()
        inc_lines = []
        in_inc = False
        for line in lines:
            if line.startswith("set(TPYC_INCLUDE_DIRS"):
                in_inc = True
                continue
            if in_inc:
                if line.strip() == ")":
                    break
                inc_lines.append(line.strip())
        assert len(inc_lines) == 2
        assert "../" in inc_lines[1] or str(runtime_inc) in inc_lines[1]

    def test_bundle_runtime_idempotent(self, tmp_path: Path):
        """Repeated calls with bundle_runtime=True succeed."""
        layout = self._make_layout(tmp_path)
        runtime_inc = get_runtime_dir() / "cpp" / "include"
        cpp_files = self._dummy_cpp(layout)
        layout.generate_cmake(
            runtime_include_dir=runtime_inc,
            cpp_files=cpp_files,
            bundle_runtime=True,
        )
        cmake_path = layout.generate_cmake(
            runtime_include_dir=runtime_inc,
            cpp_files=cpp_files,
            bundle_runtime=True,
        )
        content = cmake_path.read_text()
        assert "${CMAKE_CURRENT_LIST_DIR}/runtime/include" in content
        assert (layout.root_dir / "runtime" / "include" / "tpy" / "tpy.hpp").is_file()


def _make_skeleton_module(records=(), protocols=(), functions=(), enums=()):
    """Build a minimal CompiledModule with given exports populated
    and a matching `_skeleton_ids` snapshot, as if pre-populate had
    just run on it. No sema needed -- the verifier only consults the
    snapshot and the exports.
    """
    from .compiler import CompiledModule, ModuleExports, _SkeletonSnapshot
    from .parse import TpyModule
    from .parse.nodes import ModuleDirectives

    ast = TpyModule(
        records=[], functions=[], protocols=[], enums=[],
        top_level_stmts=[], source_lines=[], imports={},
        tpy_star_import=False, star_imports=set(), user_module_imports={},
        module_aliases={}, bare_module_imports=set(), type_aliases={},
        directives=ModuleDirectives(),
    )
    exports = ModuleExports()
    for r in records:
        exports.records[r.name] = r
    for p in protocols:
        exports.protocols[p.name] = p
    for name, fi_list in functions:
        exports.functions[name] = list(fi_list)
    for name, nominal in enums:
        exports.enums[name] = nominal

    snap = _SkeletonSnapshot(
        records=dict(exports.records),
        protocols=dict(exports.protocols),
        functions={n: (lst, lst[0]) if lst else (None, None)
                   for n, lst in exports.functions.items()},
    )
    return CompiledModule(
        name="m", path=Path("<test>"), ast=ast, exports=exports,
        is_entry_point=True, _skeleton_ids=snap,
    )


class TestSkeletonAdoptionAssertion:
    """Pins the `_verify_skeleton_adoption` invariant: every
    pre-populated skeleton object/list must be adopted in place by
    its registration entrypoint, so peer registries that captured a
    reference at bind_imports time see freshly-finalized data.

    Five of these tests use `_make_skeleton_module` to hand-build a
    minimal CompiledModule + matching snapshot and exercise the
    verifier directly, with no sema involved. The first test does a
    full real compile to confirm the production path passes the
    assertion in normal use (no false positives).
    """

    def test_correctly_adopted_real_compile(self):
        """A normal compile passes the assertion (smoke-test against
        false positives in the production pipeline)."""
        source = (
            "class A:\n"
            "    val: int\n"
            "    def __init__(self, v: int) -> None:\n"
            "        self.val = v\n"
            "def helper() -> int:\n"
            "    return 42\n"
        )
        Compiler.from_source(source).compile()  # raises if violated

    def test_record_identity_mismatch_fires(self):
        from .typesys import RecordInfo
        rec = RecordInfo(name="A", fields=[], module="m", defining_module="m")
        compiled = _make_skeleton_module(records=[rec])
        compiled.exports.records["A"] = RecordInfo(
            name="A", fields=[], module="m", defining_module="m",
        )
        with pytest.raises(AssertionError, match=r"record 'A'"):
            Compiler._verify_skeleton_adoption(compiled)

    def test_protocol_identity_mismatch_fires(self):
        from .typesys import ProtocolInfo
        proto = ProtocolInfo(name="P", methods=[], type_params=[],
                             module="m", is_dynamic=False)
        compiled = _make_skeleton_module(protocols=[proto])
        compiled.exports.protocols["P"] = ProtocolInfo(
            name="P", methods=[], type_params=[],
            module="m", is_dynamic=False,
        )
        with pytest.raises(AssertionError, match=r"protocol 'P'"):
            Compiler._verify_skeleton_adoption(compiled)

    def test_function_with_neither_list_nor_fi_identity_fires(self):
        from .typesys import FunctionInfo, VOID
        original = FunctionInfo(name="f", params=[], return_type=VOID,
                                qualified_name="m.f", originating_module="m")
        compiled = _make_skeleton_module(functions=[("f", [original])])
        compiled.exports.functions["f"] = [
            FunctionInfo(name="f", params=[], return_type=VOID,
                         qualified_name="m.f", originating_module="m"),
        ]
        with pytest.raises(AssertionError, match=r"function 'f'"):
            Compiler._verify_skeleton_adoption(compiled)

    def test_function_inner_fi_preserved_passes(self):
        # register_function adoption shape: fresh list, skeleton FI inside.
        from .typesys import FunctionInfo, VOID
        original = FunctionInfo(name="f", params=[], return_type=VOID,
                                qualified_name="m.f", originating_module="m")
        compiled = _make_skeleton_module(functions=[("f", [original])])
        compiled.exports.functions["f"] = [original]
        Compiler._verify_skeleton_adoption(compiled)

    def test_function_list_mutated_in_place_passes(self):
        # register_overload_group adoption shape: skeleton list cleared
        # and re-extended with fresh FIs; list identity preserved even
        # though the inner FI identity is gone.
        from .typesys import FunctionInfo, VOID
        original = FunctionInfo(name="g", params=[], return_type=VOID,
                                qualified_name="m.g", originating_module="m")
        compiled = _make_skeleton_module(functions=[("g", [original])])
        skeleton_list = compiled.exports.functions["g"]
        skeleton_list.clear()
        skeleton_list.extend([
            FunctionInfo(name="g", params=[], return_type=VOID,
                         qualified_name="m.g", originating_module="m"),
            FunctionInfo(name="g", params=[], return_type=VOID,
                         qualified_name="m.g", originating_module="m"),
        ])
        Compiler._verify_skeleton_adoption(compiled)

    def test_enum_not_checked(self):
        from .typesys import NominalType
        compiled = _make_skeleton_module(
            enums=[("Color", NominalType(name="Color", type_args=(),
                                         _module_qname="m.Color"))],
        )
        compiled.exports.enums["Color"] = NominalType(
            name="Color", type_args=(), _module_qname="m.Color",
        )
        Compiler._verify_skeleton_adoption(compiled)


class TestCallMacroModuleData:
    """A `@call_macro` runs during Pass 7 with no module handed to it, so the
    plugin's per-module payload must ride on the SemanticContext. Pins that
    bind_imports stashes `TpyModule.macro_data` onto the ctx and that
    `CallMacroContext.module_data` surfaces it -- the channel a macro-hosting
    plugin reads its per-module lookup table through."""

    def _bare_module(self, macro_data):
        from .parse import TpyModule
        from .parse.nodes import ModuleDirectives
        return TpyModule(
            records=[], functions=[], protocols=[], enums=[],
            top_level_stmts=[], source_lines=[], imports={},
            tpy_star_import=False, star_imports=set(), user_module_imports={},
            module_aliases={}, bare_module_imports=set(), type_aliases={},
            directives=ModuleDirectives(), macro_data=macro_data,
        )

    def test_bind_imports_stashes_macro_data_for_call_macro(self):
        """bind_imports copies the module payload onto ctx, and the call-macro
        context reads it back -- the end-to-end per-module channel."""
        from .sema import SemanticAnalyzer
        from .macro_api import CallMacroContext
        payload = {"queries": {"doubled$%": [{"mangled_name": "doubled"}]}}
        analyzer = SemanticAnalyzer()
        analyzer.bind_imports(self._bare_module(payload), "m")
        assert analyzer.ctx.macro_data is payload
        assert CallMacroContext(analyzer.ctx).module_data is payload

    def test_module_data_none_for_payloadless_module(self):
        """A parser-produced module (no plugin payload) surfaces None, not a
        crash -- the property must tolerate the common case."""
        from .sema import SemanticAnalyzer
        from .macro_api import CallMacroContext
        analyzer = SemanticAnalyzer()
        analyzer.bind_imports(self._bare_module(None), "m")
        assert CallMacroContext(analyzer.ctx).module_data is None


# Programs for the repeated-compile guard below, one per family of
# identity-keyed table: a `@property` read reaches the synthesized-getter
# seam (`expr_types`), an async try/finally the resumable seam maps
# (`THIRResumableBody`, `thir_resumables`, `resumable_region_guards`), and a
# generator the simple-generator ones (`thir_simple_gens`,
# `generator_for_loop_info`).
_CASES_DIR = Path(__file__).resolve().parent.parent / "tests" / "cases"
_REPEAT_CASES = {
    "property": _CASES_DIR / "records/member_name_shadows_type/src/main.py",
    "async": _CASES_DIR / "async/await_try_except_finally/src/main.py",
    "generator": _CASES_DIR / "iterators/gen_finally_raises_once/src/main.py",
}


class TestRepeatedCompilesAreIdentical:
    """Only the heap layout differs between two compilations of one program in
    one process, so a later compile that diagnoses differently or emits
    different C++ means a side table outlived the objects it keys on."""

    # A canary, not a proof -- the deterministic pins are in
    # `tpyc/test_identity_map.py`. Before `tpyc/identity_map.py` the property
    # case failed in about a third of runs at this count, and no more often at
    # 16 or 24: the reject lands on whichever iteration recycles the address,
    # so more repeats buy nothing.
    ITERATIONS = 8

    def _compile_once(self, src: Path) -> tuple[str, tuple[str, str]]:
        compiler = Compiler(src, default_int="Int32", lib_dirs=_STDLIB_DIRS)
        modules = compiler.compile()
        entry = next(m for m in modules if m.is_entry_point)
        diags = [d.format("tpyc") for d in compiler.diagnostics]
        for mod in modules:
            diags.extend(d.format(mod.path.name) for d in mod.analyzer.diagnostics)
        sources, _ctx = compiler.generate_code_and_thir(entry)
        return "\n".join(diags), sources

    @pytest.mark.parametrize("family", sorted(_REPEAT_CASES))
    def test_the_same_program_compiles_the_same_way_every_time(self, family):
        src = _REPEAT_CASES[family]
        if not src.exists():
            pytest.skip(f"case corpus not present at {src}")
        first_diags, first_sources = self._compile_once(src)
        assert "not yet supported" not in first_diags, first_diags
        for i in range(1, self.ITERATIONS):
            diags, sources = self._compile_once(src)
            assert diags == first_diags, (
                f"compile #{i} in this process diagnosed differently:\n"
                f"{diags}\n--- first ---\n{first_diags}")
            assert sources == first_sources, (
                f"compile #{i} in this process emitted different C++")
