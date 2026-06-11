"""Tuple-literal unpack desugar (parser) + the residual copy warning (sema).

The aliasing behavior of the desugar is exercised end-to-end by the snippet
cases under tests/cases/tuple/ and tests/cases/async/. These unit tests pin
the parser-level shape (flat function-body unpacks desugar; module-level and
non-flat forms do not) and the sema warning for the residual top-level
reference-copy case -- which can't be a snippet exec case because module-level
reference unpacks don't yet build (see BUGS.md)."""

from . import get_lib_dir
from .compiler import Compiler
from .diagnostics import DiagnosticLevel
from .parse.nodes import TpyTupleUnpack, TpyVarDecl

_STDLIB_DIRS = [get_lib_dir() / "tpy"]


def _compile(source: str):
    compiler = Compiler.from_source(source, lib_dirs=_STDLIB_DIRS)
    modules = compiler.compile()
    return compiler, modules


def _entry(modules):
    return [m for m in modules if m.is_entry_point][0]


def _entry_body(modules, func_name: str):
    fn = [f for f in _entry(modules).ast.functions if f.name == func_name][0]
    return fn.body


def _warnings(modules) -> list[str]:
    # Sema warnings are not aggregated onto the Compiler; they stay on each
    # module's own analyzer.
    return [d.message
            for m in modules
            if getattr(m, "analyzer", None) is not None
            for d in m.analyzer.diagnostics
            if d.level == DiagnosticLevel.WARNING]


class TestDesugarShape:
    """A flat function-body tuple-literal unpack lowers to per-element
    single-assigns; other forms keep TpyTupleUnpack."""

    def test_flat_function_body_unpack_desugars(self):
        # An element that can raise/suspend (here a constructor call) takes the
        # temp form: evaluate all elements first, then bind.
        _, modules = _compile(
            "from tpy import Int32\n"
            "def f() -> None:\n"
            "    a, b = (Int32(1), Int32(2))\n"
            "    print(a)\n"
            "    print(b)\n"
        )
        body = _entry_body(modules, "f")
        assert not any(isinstance(s, TpyTupleUnpack) for s in body)
        # >= 4: two element temps plus two target binds.
        assert sum(isinstance(s, TpyVarDecl) for s in body) >= 4
        assert any(isinstance(s, TpyVarDecl) and s.name.startswith("__unpack_")
                   for s in body)

    def test_simple_literal_unpack_binds_directly(self):
        # All-literal RHS with no target read in it: no temps, just the two
        # target binds.
        _, modules = _compile(
            "def f() -> None:\n"
            "    a, b = (1, 2)\n"
            "    print(a)\n"
            "    print(b)\n"
        )
        body = _entry_body(modules, "f")
        decls = [s for s in body if isinstance(s, TpyVarDecl)]
        assert {d.name for d in decls if d.name in ("a", "b")} == {"a", "b"}
        assert not any(d.name.startswith("__unpack_") for d in decls)

    def test_swap_uses_temps(self):
        # A target read in the RHS (swap) needs the all-then-bind temp form.
        _, modules = _compile(
            "def f() -> None:\n"
            "    a = 1\n"
            "    b = 2\n"
            "    a, b = (b, a)\n"
            "    print(a)\n"
            "    print(b)\n"
        )
        body = _entry_body(modules, "f")
        assert any(isinstance(s, TpyVarDecl) and s.name.startswith("__unpack_")
                   for s in body)

    def test_module_level_unpack_not_desugared(self):
        _, modules = _compile(
            "from tpy import Int32\n"
            "a, b = (Int32(1), Int32(2))\n"
            "print(a)\n"
        )
        assert any(isinstance(s, TpyTupleUnpack)
                   for s in _entry(modules).ast.top_level_stmts)

    def test_name_source_unpack_not_desugared(self):
        # Only a tuple-LITERAL RHS desugars; a name source keeps the node.
        _, modules = _compile(
            "from tpy import Int32\n"
            "def f(t: tuple[Int32, Int32]) -> None:\n"
            "    a, b = t\n"
            "    print(a)\n"
            "    print(b)\n"
        )
        body = _entry_body(modules, "f")
        assert any(isinstance(s, TpyTupleUnpack) for s in body)


class TestResidualCopyWarning:
    """A tuple-literal unpack left on the copy path (module-level) warns when
    it copies a reference-type lvalue element."""

    def test_module_level_reference_unpack_warns(self):
        _, modules = _compile(
            "from tpy import Int32\n"
            "class C:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "g0 = C(1)\n"
            "g1 = C(2)\n"
            "a, b = (g0, g1)\n"
        )
        warns = _warnings(modules)
        assert any("copies reference-type element" in w for w in warns), warns

    def test_module_level_value_unpack_does_not_warn(self):
        # Value-type elements copy correctly either way -- no warning.
        _, modules = _compile(
            "from tpy import Int32\n"
            "a, b = (Int32(1), Int32(2))\n"
            "print(a)\n"
        )
        assert not any("copies reference-type element" in w
                       for w in _warnings(modules))
