"""Tuple-literal unpack desugar (parser) + the residual copy warning (sema).

The aliasing behavior of the desugar is exercised end-to-end by the snippet
cases under tests/cases/tuple/ and tests/cases/async/. These unit tests pin
the parser-level shape: flat tuple-literal unpacks desugar to per-element
single-assigns at both function-body and module top level; non-flat forms
(name source, arity mismatch) keep TpyTupleUnpack. The sema copy warning now
guards only the macro-built path (macro_api.tuple_unpack), since every parser
producer either desugars or carries an arity error."""

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

    def test_module_level_unpack_desugars(self):
        # A module-level flat tuple-literal unpack desugars too: the global
        # single-assigns alias reference elements (CPython parity) and avoid
        # the global-unpack T*->T copy.
        _, modules = _compile(
            "from tpy import Int32\n"
            "a, b = (Int32(1), Int32(2))\n"
            "print(a)\n"
        )
        top = _entry(modules).ast.top_level_stmts
        assert not any(isinstance(s, TpyTupleUnpack) for s in top)
        assert {s.name for s in top
                if isinstance(s, TpyVarDecl) and s.name in ("a", "b")} == {"a", "b"}

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
    """The parser desugars module-level tuple-literal unpacks to aliasing
    single-assigns, so the sema copy warning no longer fires on the parser
    path -- it remains only as a guardrail for the macro-built unpack."""

    def test_module_level_reference_unpack_aliases_no_warning(self):
        # Reference elements now alias via the desugar; the residual-copy
        # warning must NOT fire (the old residual is fixed at module scope).
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
        assert not any("copies reference-type element" in w for w in warns), warns
        top = _entry(modules).ast.top_level_stmts
        assert not any(isinstance(s, TpyTupleUnpack) for s in top)

    def test_module_level_value_unpack_does_not_warn(self):
        # Value-type elements copy correctly either way -- no warning.
        _, modules = _compile(
            "from tpy import Int32\n"
            "a, b = (Int32(1), Int32(2))\n"
            "print(a)\n"
        )
        assert not any("copies reference-type element" in w
                       for w in _warnings(modules))
