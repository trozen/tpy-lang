"""Unit tests for the per-body AST-fallback tally (fallback.py): the
helpers record on the active compiler and no-op without one, the
signature gate notes first-reject reasons through a real lowering
attempt, and classify_stmt tags landmark constructs."""

from __future__ import annotations

from ..compilation_context import _current_compiler, activate_compiler
from . import fallback as _fb
from .fallback import (
    arm_universe,
    begin_attempt,
    begin_stmt,
    classify_stmt,
    fold_attempt,
    note,
    note_detail,
    record_arm_residual,
    stmt_reject_reason,
)
from .lower import (
    iter_module_callables,
    iter_module_constructors,
    lower_constructor,
    lower_function,
)
from .lower.predicates import _type_family_tag
from .testutil import _compile, _entry


def test_note_and_fold_record_on_active_compiler():
    compiler, _ = _compile("def f() -> None:\n    pass\n")
    with activate_compiler(compiler):
        begin_attempt()
        assert note("sig.async") is False
        # Set-if-empty: the first recorded reason wins the attempt.
        assert note("stmt.for_each") is False
        fold_attempt("body")
        begin_attempt()
        fold_attempt("ctor")  # no reason recorded -> unclassified
    assert compiler._thir_fallback == {
        "body:sig.async": 1,
        "ctor:unclassified": 1,
    }


def test_noop_without_active_compiler():
    # Outside a compilation the helpers record nowhere; note still returns
    # False for gate positions. The autouse fixture activates a stub
    # compiler, so clear the ContextVar explicitly.
    token = _current_compiler.set(None)
    try:
        begin_attempt()
        assert note("sig.async") is False
        fold_attempt("body")
    finally:
        _current_compiler.reset(token)


def _fn_body(src, name):
    compiler, modules = _compile(src)
    entry = _entry(modules)
    f = next(fn for fn, _ in iter_module_callables(entry.ast, entry.analyzer)
             if fn.name == name)
    return compiler, entry, f


def test_arm_residual_records_deduped(monkeypatch):
    # The construct is counted ONCE per fallback unit even with two for-loops
    # -- the residual is "units containing the construct", not occurrences.
    monkeypatch.setattr(_fb, "_ARM_RESIDUAL_ON", True)
    compiler, _, f = _fn_body(
        "from tpy import Int32\n"
        "def f(xs: list[Int32]) -> Int32:\n"
        "    t = 0\n"
        "    for a in xs:\n        t += a\n"
        "    for b in xs:\n        if b > 0:\n            t += b\n"
        "    return t\n", "f")
    with activate_compiler(compiler):
        record_arm_residual(f.body)
    r = compiler._thir_arm_residual
    assert r["for_each"] == 1   # two loops -> deduped to one unit
    assert r["if"] == 1 and r["return"] == 1


def test_arm_residual_sees_nested_container_fields(monkeypatch):
    # A class-pattern KEYWORD sub-pattern hangs off `list[tuple[str, pattern]]`,
    # so a walk that only recurses into node-valued list elements never reaches
    # it and the arm reads as deletable when it is not.
    monkeypatch.setattr(_fb, "_ARM_RESIDUAL_ON", True)
    compiler, _, f = _fn_body(
        "from tpy import Int32\n"
        "class Dog:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
        "def f(d: Dog) -> Int32:\n"
        "    match d:\n"
        "        case Dog(n=k):\n            return k\n"
        "    return 0\n", "f")
    with activate_compiler(compiler):
        record_arm_residual(f.body)
    r = compiler._thir_arm_residual
    assert r["match"] == 1
    assert r["capture_pattern"] == 1


def test_arm_universe_covers_what_the_walk_names(monkeypatch):
    # The census counts only OBSERVED kinds, so a residual-0 arm is absent
    # rather than zero -- the universe is what makes zero reportable. Any kind
    # the walk can name must be in it, or that arm's 0 is unreportable.
    monkeypatch.setattr(_fb, "_ARM_RESIDUAL_ON", True)
    compiler, _, f = _fn_body(
        "from tpy import Int32\n"
        "def f(xs: list[Int32]) -> Int32:\n"
        "    t = 0\n"
        "    with open('/dev/null') as fh:\n        pass\n"
        "    try:\n"
        "        for a in xs:\n            t += a\n"
        "    except ValueError as e:\n        raise\n"
        "    print(f'{t}')\n"
        "    return t\n", "f")
    with activate_compiler(compiler):
        record_arm_residual(f.body)
    named = set(compiler._thir_arm_residual)
    # Pin the BASELESS group specifically: it is the part of the universe that
    # cannot be derived, so it is the part that silently rots.
    assert {"except_handler", "with_item", "f_string_value"} <= named
    assert named <= arm_universe(), sorted(named - arm_universe())


def test_arm_universe_excludes_non_body_nodes():
    # Decl containers and type-annotation refs are never reached by a BODY
    # walk, so seeding them would park them at 0 forever and report non-arms
    # as deletable.
    u = arm_universe()
    for kind in ("module", "record", "protocol", "enum",
                 "type_ref", "union_ref", "callable_ref"):
        assert kind not in u
    # ...while the baseless body dataclasses, which have no derivable base,
    # ARE present.
    for kind in ("except_handler", "comprehension_generator", "with_item",
                 "match_case", "f_string_value", "function"):
        assert kind in u


def test_arm_residual_inert_when_gate_off(monkeypatch):
    monkeypatch.setattr(_fb, "_ARM_RESIDUAL_ON", False)
    compiler, _, f = _fn_body(
        "from tpy import Int32\n"
        "def f(xs: list[Int32]) -> None:\n    for x in xs:\n        pass\n", "f")
    with activate_compiler(compiler):
        record_arm_residual(f.body)
    assert compiler._thir_arm_residual == {}


def test_arm_residual_noop_without_active_compiler(monkeypatch):
    monkeypatch.setattr(_fb, "_ARM_RESIDUAL_ON", True)
    token = _current_compiler.set(None)
    try:
        record_arm_residual([])  # no active compiler -> no error, no record
    finally:
        _current_compiler.reset(token)


_SRC = (
    "from typing import Iterator\n"
    "from tpy import Array, Int32\n"
    "async def af() -> None:\n"
    "    pass\n"
    "def gen() -> Iterator[tuple[Int32, Int32]]:\n"
    "    yield (1, 2)\n"
    "def comp(src: Array[Int32, 3]) -> Int32:\n"
    # UNPACK comp over a generator call: the genfac comp arm takes only
    # unpack-less value-yielding sources -- this shape stays AST
    "    xs = [a + b for a, b in gen()]\n"
    "    return len(xs)\n"
    "def ok(n: Int32) -> Int32:\n"
    "    return n + 1\n"
)


def test_end_to_end_first_reject_reasons():
    compiler, modules = _compile(_SRC)
    entry = _entry(modules)
    routed = []
    with activate_compiler(compiler):
        for func, self_type in iter_module_callables(entry.ast, entry.analyzer):
            begin_attempt()
            fn = lower_function(func, entry.analyzer, self_type=self_type)
            if fn is None:
                fold_attempt("body")
            else:
                routed.append(fn.name)
    fb = compiler._thir_fallback
    assert fb.get("body:sig.async") == 1
    # The comprehension local is the first-rejecting statement; the landmark
    # scan names the frontier, not the host statement shape.
    assert fb.get("body:expr.list_comp") == 1
    assert "ok" in routed


def test_function_lowering_reject_falls_back_without_scope_residue():
    # A nested def with a param default is a durable mid-body reject
    # (`nesteddef.param_default` -- the lambda emit has no default-arg
    # spelling). Multi-target del, the previous vehicle, routes now.
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(d: dict[Int32, Int32]) -> Int32:\n"
        "    def g(a: Int32 = 1) -> Int32:\n"
        "        return a\n"
        "    return g(2)\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
    entry = _entry(modules)
    routed = []
    with activate_compiler(compiler):
        for func, self_type in iter_module_callables(entry.ast, entry.analyzer):
            begin_attempt()
            fn = lower_function(func, entry.analyzer, self_type=self_type)
            if fn is None:
                fold_attempt("body")
            else:
                routed.append(fn.name)
    assert compiler._thir_fallback.get(
        "body:stmt.nested_def:nesteddef.param_default") == 1
    assert "clean" in routed


def test_constructor_lowering_reject_falls_back():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "class R:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32):\n"
        "        self.n = n\n"
        "        def g(a: Int32 = 1) -> Int32:\n"
        "            return a\n"
        "        self.n = g(2)\n"
    )
    entry = _entry(modules)
    with activate_compiler(compiler):
        record, init, self_type = next(
            iter_module_constructors(entry.ast, entry.analyzer))
        begin_attempt()
        ctor = lower_constructor(
            record, init, entry.analyzer, self_type=self_type)
        if ctor is None:
            fold_attempt("ctor")
    assert ctor is None
    assert compiler._thir_fallback.get(
        "ctor:stmt.nested_def:nesteddef.param_default") == 1


def test_base_init_arg_lowering_reject_falls_back():
    # A base-init arg is admitted by TYPE (`_base_init_arg_ok`), so a
    # structurally-unhandled scalar shape reaches `_lower_expr` and only rejects
    # there -- it must land on the ctor's fallback boundary, not escape as a crash.
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def eat(xs: list[Int32]) -> Int32:\n"
        "    xs.append(1)\n"
        "    return len(xs)\n"
        "class Base:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32):\n"
        "        self.x = x\n"
        "class Derived(Base):\n"
        "    y: Int32\n"
        "    def __init__(self, n: Int32):\n"
        "        super().__init__(eat([1, 2]))\n"
        "        self.y = n\n"
    )
    entry = _entry(modules)
    with activate_compiler(compiler):
        ctors = {rec.name: (rec, init, st) for rec, init, st
                 in iter_module_constructors(entry.ast, entry.analyzer)}
        record, init, self_type = ctors["Derived"]
        begin_attempt()
        ctor = lower_constructor(
            record, init, entry.analyzer, self_type=self_type)
        if ctor is None:
            fold_attempt("ctor")
    assert ctor is None
    assert compiler._thir_fallback.get("ctor:expr.call") == 1


def test_global_lowering_reject_falls_back_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "message = b'before'\n"
        "def rejected(n: Int32) -> Int32:\n"
        "    global message\n"
        "    return n\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
    entry = _entry(modules)
    routed = []
    with activate_compiler(compiler):
        for func, self_type in iter_module_callables(entry.ast, entry.analyzer):
            begin_attempt()
            fn = lower_function(func, entry.analyzer, self_type=self_type)
            if fn is None:
                fold_attempt("body")
            else:
                routed.append(fn.name)
    assert compiler._thir_fallback.get(
        "body:stmt.global:global.unseeded") == 1
    assert "clean" in routed


def test_global_lowering_reject_falls_back_at_constructor_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "message = b'before'\n"
        "class R:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32):\n"
        "        global message\n"
        "        self.n = n\n"
    )
    entry = _entry(modules)
    with activate_compiler(compiler):
        record, init, self_type = next(
            iter_module_constructors(entry.ast, entry.analyzer))
        begin_attempt()
        ctor = lower_constructor(
            record, init, entry.analyzer, self_type=self_type)
        if ctor is None:
            fold_attempt("ctor")
    assert ctor is None
    assert compiler._thir_fallback.get(
        "ctor:stmt.global:global.unseeded") == 1


def test_raise_lowering_reject_falls_back_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "err = ValueError('bad')\n"
        "def rejected(n: Int32) -> Int32:\n"
        "    if n > 0:\n"
        "        raise err\n"
        "    err = ValueError('x')\n"
        "    raise err\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
    entry = _entry(modules)
    routed = []
    with activate_compiler(compiler):
        for func, self_type in iter_module_callables(entry.ast, entry.analyzer):
            begin_attempt()
            fn = lower_function(func, entry.analyzer, self_type=self_type)
            if fn is None:
                fold_attempt("body")
            else:
                routed.append(fn.name)
    # The later local SHADOW excludes `err` from seeding (whole-function
    # scan), so the read-before-shadow raise-source rejects -- the nested
    # reason folds the body back at the sync boundary while `clean` stays
    # routed. (An un-shadowed record global raise routes via the slot
    # seeding now.)
    assert compiler._thir_fallback.get("body:stmt.raise:name.global_read") == 1
    assert "clean" in routed


def test_wide_integer_routes_while_nonfinite_float_rejects():
    compiler, modules = _compile(
        "from tpy import Float64, Int64\n"
        "def take_int(n: Int64) -> Int64:\n"
        "    return n\n"
        "def take_float(n: Float64) -> Float64:\n"
        "    return n\n"
        "def wide() -> Int64:\n"
        "    return take_int(2147483648)\n"
        "def nonfinite() -> Float64:\n"
        "    return take_float(1e400)\n"
    )
    entry = _entry(modules)
    routed = []
    with activate_compiler(compiler):
        for func, self_type in iter_module_callables(entry.ast, entry.analyzer):
            begin_attempt()
            fn = lower_function(func, entry.analyzer, self_type=self_type)
            if fn is None:
                fold_attempt("body")
            else:
                routed.append(fn.name)
    assert "wide" in routed
    assert compiler._thir_fallback.get(
        "body:expr.float_literal.nonfinite") == 1


def test_unhandled_expression_rejects_from_lowering_tail():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(s: str) -> str:\n"
        "    x = (y := s + \"!\")\n"
        "    return x\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
    entry = _entry(modules)
    routed = []
    with activate_compiler(compiler):
        for func, self_type in iter_module_callables(entry.ast, entry.analyzer):
            begin_attempt()
            fn = lower_function(func, entry.analyzer, self_type=self_type)
            if fn is None:
                fold_attempt("body")
            else:
                routed.append(fn.name)
    assert compiler._thir_fallback.get("body:expr.walrus") == 1
    assert "clean" in routed


def test_while_bool_literal_routes_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(n: Int32) -> Int32:\n"
        "    while True:\n"
        "        n = n + 1\n"
        "    return n\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
    entry = _entry(modules)
    routed = []
    with activate_compiler(compiler):
        for func, self_type in iter_module_callables(entry.ast, entry.analyzer):
            begin_attempt()
            fn = lower_function(func, entry.analyzer, self_type=self_type)
            if fn is None:
                fold_attempt("body")
            else:
                routed.append(fn.name)
    assert compiler._thir_fallback == {}
    assert "rejected" in routed
    assert "clean" in routed


def test_while_bool_literal_routes_at_constructor_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "class R:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32):\n"
        "        while True:\n"
        "            n = n + 1\n"
        "        self.n = n\n"
    )
    entry = _entry(modules)
    with activate_compiler(compiler):
        record, init, self_type = next(
            iter_module_constructors(entry.ast, entry.analyzer))
        begin_attempt()
        ctor = lower_constructor(
            record, init, entry.analyzer, self_type=self_type)
        if ctor is None:
            fold_attempt("ctor")
    assert ctor is not None
    assert compiler._thir_fallback == {}


def test_for_lowering_reject_falls_back_at_sync_boundary():
    # A hoisted loop var whose element is itself a CONTAINER: the reassigned
    # nested-element local binds only the single-assignment `T&` alias, so the
    # body falls back whole. (The plain-record flavor of this shape routes now
    # -- its element lifts to a reseatable `T*`.)
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "class R:\n    v: Int32\n"
        "    def __init__(self, v: Int32):\n        self.v = v\n"
        "def rejected(m: list[list[R]]) -> Int32:\n"
        "    keep = m[0]\n"
        "    for row in m:\n"
        "        keep = row\n"
        "    return Int32(len(keep))\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
    entry = _entry(modules)
    routed = []
    with activate_compiler(compiler):
        for func, self_type in iter_module_callables(entry.ast, entry.analyzer):
            begin_attempt()
            fn = lower_function(func, entry.analyzer, self_type=self_type)
            if fn is None:
                fold_attempt("body")
            else:
                routed.append(fn.name)
    # Reason-agnostic (for-loop reject reasons shift as rungs are ported):
    # the rejected body falls back whole, the clean sibling still routes.
    assert "rejected" not in routed
    assert "clean" in routed


def test_for_each_generator_call_iterable_routes():
    # A free generator-call iterable takes the iter_proto route (the
    # universal __iter__/__next__ loop) -- routed, no sub-tag.
    compiler, entry, f = _fn_body(
        "from tpy import Int32\n"
        "from typing import Iterator\n"
        "def gen(n: Int32) -> Iterator[Int32]:\n"
        "    yield n\n"
        "def routed(n: Int32) -> Int32:\n"
        "    t = 0\n"
        "    for x in gen(n):\n"
        "        t = t + x\n"
        "    return t\n",
        "routed")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
    assert fn is not None
    assert compiler._thir_face_witnesses.get("foreach.iter_proto") == 1


def test_for_each_gen_call_container_literal_arg_routes():
    # A container-literal arg on the iter_proto iterable call is a
    # temp-hoisting row: the emit flushes the temp inside the rvalue brace
    # scope right before the `__src` bind (the AST's for-each flush point).
    import io

    from .emit import emit_thir_body

    compiler, entry, f = _fn_body(
        "from tpy import Int32\n"
        "from typing import Iterator\n"
        "def gen(xs: list[Int32]) -> Iterator[Int32]:\n"
        "    for x in xs:\n"
        "        yield x\n"
        "def routed() -> Int32:\n"
        "    t = 0\n"
        "    for x in gen([1, 2, 3]):\n"
        "        t = t + x\n"
        "    return t\n",
        "routed")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
        assert fn is not None
        buf = io.StringIO()
        emit_thir_body(buf, fn)
    body = buf.getvalue()
    flush = body.index("std::vector<int32_t> __tmp_1 = {1, 2, 3};")
    src = body.index("auto __src_0 = gen(__tmp_1);")
    scope = body.index("{\n")
    assert scope < flush < src


def test_iterator_object_decl_routes():
    # `it = g()` -> `auto it = g();` (decl.iterator_object) and the for-head
    # admits the LOCAL despite its protocol declared type (a protocol PARAM
    # stays deferred -- pinned below).
    import io

    from .emit import emit_thir_body

    compiler, entry, f = _fn_body(
        "from tpy import Int32\n"
        "from typing import Iterator\n"
        "def gen(n: Int32) -> Iterator[Int32]:\n"
        "    yield n\n"
        "def routed(n: Int32) -> Int32:\n"
        "    it = gen(n)\n"
        "    t = 0\n"
        "    for v in it:\n"
        "        t = t + v\n"
        "    return t\n",
        "routed")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
        assert fn is not None
        buf = io.StringIO()
        emit_thir_body(buf, fn)
    body = buf.getvalue()
    assert "auto it = gen(n);\n" in body
    assert "auto& __src_0 = it;\n" in body
    assert compiler._thir_face_witnesses.get("decl.iterator_object") == 1


def test_iterator_protocol_param_iterable_routes():
    # An Iterator[T]-typed STRUCTURAL param iterable routes in a sync body:
    # the deduced `T_it&` is a plain lvalue, the universal `__iter__` loop
    # renders identically to a user-iterator record name (corpus-verified on
    # iterators/for_*_protocol). NativeIterable/Spannable, Own-element, and
    # resumable shapes keep deferring (see the still-defers pins).
    compiler, entry, f = _fn_body(
        "from tpy import Int32\n"
        "from typing import Iterator\n"
        "def routed(it: Iterator[Int32]) -> Int32:\n"
        "    t = 0\n"
        "    for v in it:\n"
        "        t = t + v\n"
        "    return t\n",
        "routed")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
    assert fn is not None


def test_protocol_arg_pending_type_resolves():
    # A literal-seeded local passed into a protocol param slot is still
    # PENDING at lowering. Sema's resolution is final by then, so asking for
    # it here gives the same type the AST reaches at its own later render
    # point -- the row routes rather than falling back. The residual
    # `PendingListType` guard stays for an UNRESOLVABLE binding, but NO test
    # pins it: no TPy program reaching this arm is known to leave a binding
    # unresolvable after `resolve_pending_container`, so the guard is
    # defensive-only. Tracked in TODO.md; write the pin if a shape appears.
    compiler, entry, f = _fn_body(
        "from typing import Iterator, Iterable\n"
        "def echo(it: Iterable[int]) -> Iterator[int]:\n"
        "    for x in it:\n"
        "        yield x\n"
        "def rejected() -> None:\n"
        "    xs = [1, 2]\n"
        "    g = echo(xs)\n"
        "    for v in g:\n"
        "        print(v)\n",
        "rejected")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
        if fn is None:
            fold_attempt("body")
    assert fn is not None


def test_for_each_gen_call_record_rvalue_arg_routes():
    # A record-ctor rvalue arg is a DISTINCT temp row (_record_rvalue_temp_arg)
    # from the container-literal one -- pin it at the iter_proto iterable too.
    import io

    from .emit import emit_thir_body

    compiler, entry, f = _fn_body(
        "from tpy import Int32\n"
        "from typing import Iterator\n"
        "class Rec:\n"
        "    v: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.v = v\n"
        "def gen(r: Rec) -> Iterator[Int32]:\n"
        "    yield r.v\n"
        "def routed() -> Int32:\n"
        "    t = 0\n"
        "    for x in gen(Rec(7)):\n"
        "        t = t + x\n"
        "    return t\n",
        "routed")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
        assert fn is not None
        buf = io.StringIO()
        emit_thir_body(buf, fn)
    body = buf.getvalue()
    assert body.index("__tmp_1") < body.index("auto __src_0 = gen(__tmp_1);")


def test_for_each_container_route_literal_arg_still_rejects():
    # The begin/end container route's iterable renders into the loop header
    # -- NO flush point -- so a temp-hoisting arg there must keep rejecting
    # (widening it would be the stale-snapshot miscompile the validator
    # guards). Only the iter_proto route admits temps.
    compiler, entry, f = _fn_body(
        "from tpy import Int32, Own\n"
        "def make(xs: list[Int32]) -> Own[list[Int32]]:\n"
        "    return [x * 2 for x in xs]\n"
        "def rejected() -> Int32:\n"
        "    t = 0\n"
        "    for x in make([1, 2]):\n"
        "        t = t + x\n"
        "    return t\n",
        "rejected")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
        if fn is None:
            fold_attempt("body")
    assert fn is None
    assert compiler._thir_fallback.get("body:expr.call") == 1


def test_for_each_method_generator_call_routes():
    # A bare-name-receiver member generator call takes the iter_proto route
    # (the _member_gen_call_iterable_ok override).
    compiler, entry, f = _fn_body(
        "from tpy import Int32\n"
        "from typing import Iterator\n"
        "class Maker:\n"
        "    base: Int32\n"
        "    def __init__(self, b: Int32) -> None:\n"
        "        self.base = b\n"
        "    def gen(self, n: Int32) -> Iterator[Int32]:\n"
        "        yield self.base + n\n"
        "def routed(m: Maker, n: Int32) -> Int32:\n"
        "    t = 0\n"
        "    for x in m.gen(n):\n"
        "        t = t + x\n"
        "    return t\n",
        "routed")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
    assert fn is not None
    assert compiler._thir_face_witnesses.get("foreach.iter_proto") == 1


def test_for_each_field_recv_generator_call_defers():
    # A FIELD-receiver generator call stays outside the iter_proto slice
    # (bare-name receivers only): the route accepts the shape, so the reject
    # now fires at the member arm's fi gate (expr.method_call), not the
    # route classifier's iter.call.generator tag.
    compiler, entry, f = _fn_body(
        "from tpy import Int32, Own\n"
        "from typing import Iterator\n"
        "class Maker:\n"
        "    base: Int32\n"
        "    def __init__(self, b: Int32) -> None:\n"
        "        self.base = b\n"
        "    def gen(self, n: Int32) -> Iterator[Int32]:\n"
        "        yield self.base + n\n"
        "class Holder:\n"
        "    maker: Maker\n"
        "    def __init__(self, maker: Own[Maker]) -> None:\n"
        "        self.maker = maker\n"
        "def rejected(h: Holder, n: Int32) -> Int32:\n"
        "    t = 0\n"
        "    for x in h.maker.gen(n):\n"
        "        t = t + x\n"
        "    return t\n",
        "rejected")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
        if fn is None:
            fold_attempt("body")
    assert fn is None
    assert compiler._thir_fallback.get("body:expr.method_call") == 1


def test_for_each_user_iterator_name_routes():
    # A concrete user-iterator param name takes the iter_proto route (the
    # lvalue `auto& __src_N` capture).
    compiler, entry, f = _fn_body(
        "from __future__ import annotations\n"
        "from tpy import Int32\n"
        "class Ticker:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "    def __iter__(self) -> Ticker:\n"
        "        return self\n"
        "    def __next__(self) -> Int32:\n"
        "        if self.n <= 0:\n"
        "            raise StopIteration\n"
        "        self.n -= 1\n"
        "        return self.n\n"
        "def routed(t: Ticker) -> Int32:\n"
        "    total = 0\n"
        "    for v in t:\n"
        "        total = total + v\n"
        "    return total\n",
        "routed")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
    assert fn is not None
    assert compiler._thir_face_witnesses.get("foreach.iter_proto") == 1


def test_for_each_spannable_protocol_param_routes_begin_end():
    # The Spannable sibling of the NativeIterable peephole: a
    # `tpy.Spannable[T]` param takes the begin/end range-for (records.py
    # synthesizes begin()/end() for conformers), mirrored via the
    # container route -- routed since the native-proto-param widening.
    compiler, entry, f = _fn_body(
        "from tpy import Int32, Spannable\n"
        "def routed(it: Spannable[Int32]) -> Int32:\n"
        "    total = 0\n"
        "    for v in it:\n"
        "        total = total + v\n"
        "    return total\n",
        "routed")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
        if fn is None:
            fold_attempt("body")
    assert fn is not None


def test_for_each_own_elem_protocol_param_still_defers():
    # An `Own[...]`-ELEMENT protocol loop takes the consuming element render
    # on the AST path -- must keep deferring (corpus caught the divergence on
    # auto_move/consuming_iterable_own_param mid-wave).
    compiler, entry, f = _fn_body(
        "from tpy import Int32, Own\n"
        "from typing import Iterable\n"
        "class Item:\n"
        "    v: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
        "def rejected(source: Iterable[Own[Item]]) -> Int32:\n"
        "    total = 0\n"
        "    for x in source:\n"
        "        total = total + x.v\n"
        "    return total\n",
        "rejected")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
        if fn is None:
            fold_attempt("body")
    assert fn is None


def test_for_each_native_iterable_protocol_param_routes_begin_end():
    # A `tpy.NativeIterable[T]` param takes the AST's begin/end range-for
    # peephole, mirrored via the container route (THIRForEach) -- routed
    # since the native-proto-param widening; byte-identity is pinned by
    # iterators/iterable_native_narrowing_bare and the wave test file.
    compiler, entry, f = _fn_body(
        "from tpy import Int32, NativeIterable\n"
        "def routed(it: NativeIterable[Int32]) -> Int32:\n"
        "    total = 0\n"
        "    for v in it:\n"
        "        total = total + v\n"
        "    return total\n",
        "routed")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
        if fn is None:
            fold_attempt("body")
    assert fn is not None


def test_for_each_plain_call_iterable_routes():
    # A NON-generator callee returning a user-iterator record takes the
    # same universal-loop route as its generator sibling (the Own[...]
    # return is an rvalue -- the owning `auto __src_N` capture).
    compiler, entry, f = _fn_body(
        "from __future__ import annotations\n"
        "from tpy import Int32, Own\n"
        "class Ticker:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "    def __iter__(self) -> Ticker:\n"
        "        return self\n"
        "    def __next__(self) -> Int32:\n"
        "        if self.n <= 0:\n"
        "            raise StopIteration\n"
        "        self.n -= 1\n"
        "        return self.n\n"
        "def make_ticker(n: Int32) -> Own[Ticker]:\n"
        "    return Ticker(n)\n"
        "def routed(n: Int32) -> Int32:\n"
        "    t = 0\n"
        "    for x in make_ticker(n):\n"
        "        t = t + x\n"
        "    return t\n",
        "routed")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
    assert fn is not None
    assert compiler._thir_face_witnesses.get("foreach.iter_proto") == 1


def test_for_each_iterator_ctor_call_iterable_routes():
    # A user-iterator CTOR call as the iterable (`for x in SimpleIter(4):`)
    # -- a record-ctor rvalue into the same universal loop.
    compiler, entry, f = _fn_body(
        "from __future__ import annotations\n"
        "from tpy import Int32\n"
        "class Ticker:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "    def __iter__(self) -> Ticker:\n"
        "        return self\n"
        "    def __next__(self) -> Int32:\n"
        "        if self.n <= 0:\n"
        "            raise StopIteration\n"
        "        self.n -= 1\n"
        "        return self.n\n"
        "def routed(n: Int32) -> Int32:\n"
        "    t = 0\n"
        "    for x in Ticker(n):\n"
        "        t = t + x\n"
        "    return t\n",
        "routed")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
    assert fn is not None
    assert compiler._thir_face_witnesses.get("foreach.iter_proto") == 1


def test_for_each_container_returning_call_stays_container_route():
    # A list-returning call is NativeIterable: _iter_proto_call_ret must NOT
    # claim it -- it keeps the container route's begin/end emit.
    compiler, entry, f = _fn_body(
        "from tpy import Int32, Own\n"
        "def make() -> Own[list[Int32]]:\n"
        "    return [1, 2]\n"
        "def routed() -> Int32:\n"
        "    t = 0\n"
        "    for x in make():\n"
        "        t = t + x\n"
        "    return t\n",
        "routed")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
    assert fn is not None
    assert not compiler._thir_face_witnesses.get("foreach.iter_proto")


def test_for_each_tuple_unpack_over_gen_call_routes():
    # `for a, b in gen():` -- the tuple-unpack head rides the universal
    # __iter__/__next__ loop (THIRForIterProto) for scalar targets.
    compiler, entry, f = _fn_body(
        "from tpy import Int32\n"
        "from typing import Iterator\n"
        "def pairs(n: Int32) -> Iterator[tuple[Int32, Int32]]:\n"
        "    for i in range(n):\n"
        "        yield (i, i * 2)\n"
        "def routed(n: Int32) -> Int32:\n"
        "    t = 0\n"
        "    for a, b in pairs(n):\n"
        "        t = t + a + b\n"
        "    return t\n",
        "routed")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
    assert fn is not None
    assert compiler._thir_face_witnesses.get("foreach.tuple_unpack_iter") == 1


def test_for_each_tuple_unpack_over_gen_record_target_routes():
    # A record unpack target over a generator call ROUTES: the iter-proto
    # element is already a borrow-form tuple, so the ref target aliases via
    # unwrap_ref/tuple_elem_ref off the mutable `auto& __tup_N` head
    # (corpus witness: iterators/gen_resumable_mixed_tuple_yield).
    compiler, entry, f = _fn_body(
        "from tpy import Int32\n"
        "from typing import Iterator\n"
        "class P:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "def gen(ps: list[P]) -> Iterator[tuple[Int32, P]]:\n"
        "    for i in range(len(ps)):\n"
        "        yield (Int32(i), ps[i])\n"
        "def routed(ps: list[P]) -> Int32:\n"
        "    t = 0\n"
        "    for i, p in gen(ps):\n"
        "        t = t + i + p.x\n"
        "    return t\n",
        "routed")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
        if fn is None:
            fold_attempt("body")
    assert fn is not None
    assert compiler._thir_face_witnesses.get(
        "stmt.tuple_unpack.ref_target_iter", 0) >= 1


def test_for_each_tuple_unpack_ref_target_routes():
    # A for-head unpack over a storage-form list CONTAINER binds a value scalar
    # target and aliases a record element target (`is_ref`) via the loop
    # element's tuple_to_pointer lift -- routes (was a deferred ref-target rung).
    # A generator source keeps deferring (see the _over_gen_ test above).
    compiler, entry, f = _fn_body(
        "from tpy import Int32\n"
        "class Point:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "def routed(pairs: list[tuple[Int32, Point]]) -> Int32:\n"
        "    total = 0\n"
        "    for i, p in pairs:\n"
        "        total = total + i + p.x\n"
        "    return total\n",
        "routed")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
    assert fn is not None


def test_tuple_unpack_lowering_reject_falls_back_at_sync_boundary():
    # A REFERENCE-element tuple keeps the unpack source rejecting (only
    # VALUE-tuple elements render bare at the capture).
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "class Box:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
        "def rejected(pairs: list[tuple[Int32, Box]]) -> Int32:\n"
        "    a, b = pairs[0]\n"
        "    return a + b.n\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
    entry = _entry(modules)
    routed = []
    with activate_compiler(compiler):
        for func, self_type in iter_module_callables(entry.ast, entry.analyzer):
            begin_attempt()
            fn = lower_function(func, entry.analyzer, self_type=self_type)
            if fn is None:
                fold_attempt("body")
            else:
                routed.append(fn.name)
    # The reject composes from the subscript source's element read now
    # (the ref-target unpack admits ptr-Optional-element subscript sources,
    # so a plain-record-member tuple rejects one level deeper, with the
    # drilldown detail appended to the tag).
    assert sum(n for k, n in compiler._thir_fallback.items()
               if k.startswith("body:stmt.tuple_unpack")) == 1
    assert "clean" in routed


def test_expr_stmt_lowering_reject_falls_back_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(n: Int32) -> Int32:\n"
        "    n + 1\n"
        "    return n\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
    entry = _entry(modules)
    routed = []
    with activate_compiler(compiler):
        for func, self_type in iter_module_callables(entry.ast, entry.analyzer):
            begin_attempt()
            fn = lower_function(func, entry.analyzer, self_type=self_type)
            if fn is None:
                fold_attempt("body")
            else:
                routed.append(fn.name)
    assert compiler._thir_fallback.get(
        "body:stmt.expr_stmt:expr_stmt.bin_op") == 1
    assert "clean" in routed


def test_aug_assign_lowering_reject_falls_back_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(s: str) -> Int32:\n"
        "    s += 'x'\n"
        "    return Int32(len(s))\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
    entry = _entry(modules)
    routed = []
    with activate_compiler(compiler):
        for func, self_type in iter_module_callables(entry.ast, entry.analyzer):
            begin_attempt()
            fn = lower_function(func, entry.analyzer, self_type=self_type)
            if fn is None:
                fold_attempt("body")
            else:
                routed.append(fn.name)
    assert compiler._thir_fallback.get("body:stmt.aug_assign") == 1
    assert "clean" in routed


def test_assert_lowering_routes_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(n: Int32) -> Int32:\n"
        "    assert True\n"
        "    return n\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
    entry = _entry(modules)
    routed = []
    with activate_compiler(compiler):
        for func, self_type in iter_module_callables(entry.ast, entry.analyzer):
            begin_attempt()
            fn = lower_function(func, entry.analyzer, self_type=self_type)
            if fn is None:
                fold_attempt("body")
            else:
                routed.append(fn.name)
    assert compiler._thir_fallback == {}
    assert "rejected" in routed
    assert "clean" in routed


_ASSERT_MSG_RECORD = (
    "from tpy import Int32\n"
    "class E:\n"
    "    message: str\n"
    "    def __init__(self, message: str):\n"
    "        self.message = message\n"
)


def test_assert_message_clean_field_routes():
    compiler, entry, f = _fn_body(
        _ASSERT_MSG_RECORD
        + "def checked(n: Int32, e: E) -> Int32:\n"
        "    assert n > 0, e.message\n"
        "    return n\n",
        "checked")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
    assert fn is not None
    assert compiler._thir_fallback == {}


def test_assert_message_optional_field_falls_back():
    # Unproven Optional receiver: the AST render is
    # `::tpy::deref_check(e).message` -- out of the admitted slice.
    compiler, entry, f = _fn_body(
        _ASSERT_MSG_RECORD
        + "def rejected(n: Int32, e: E | None) -> Int32:\n"
        "    assert n > 0, e.message\n"
        "    return n\n",
        "rejected")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
        if fn is None:
            fold_attempt("body")
    assert fn is None
    assert compiler._thir_fallback == {"body:stmt.assert": 1}


def test_assert_message_property_field_falls_back():
    # A property message is a getter CALL on the AST path, not a member read.
    compiler, entry, f = _fn_body(
        "from tpy import Int32\n"
        "class F:\n"
        "    _m: str\n"
        "    def __init__(self, m: str):\n"
        "        self._m = m\n"
        "    @property\n"
        "    def msg(self) -> str:\n"
        "        return self._m\n"
        "def rejected(n: Int32, f: F) -> Int32:\n"
        "    assert n > 0, f.msg\n"
        "    return n\n",
        "rejected")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
        if fn is None:
            fold_attempt("body")
    assert fn is None
    assert compiler._thir_fallback == {"body:stmt.assert": 1}


_DELATTR_RECORD = (
    "from tpy import Int32\n"
    "from typing import Any\n"
    "class Bag:\n"
    "    _data: dict[str, Any]\n"
    "    def __init__(self):\n"
    "        d: dict[str, Any] = {}\n"
    "        self._data = d\n"
    "    def __delattr__(self, name: str) -> None:\n"
    "        del self._data[name]\n"
)


def test_del_attr_routes_at_sync_boundary():
    compiler, entry, f = _fn_body(
        _DELATTR_RECORD + "def drop(b: Bag) -> None:\n    del b.x\n",
        "drop")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
    assert fn is not None
    assert compiler._thir_fallback == {}


def test_del_attr_multi_target_falls_back():
    compiler, entry, f = _fn_body(
        _DELATTR_RECORD + "def drop(b: Bag) -> None:\n    del b.x, b.y\n",
        "drop")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
        if fn is None:
            fold_attempt("body")
    assert fn is None
    assert compiler._thir_fallback == {
        "body:stmt.del_attr:del_attr.multi_target": 1}


def test_del_attr_unresolved_falls_back():
    # Defensive: sema always sets dyn_delattr_call or errors; clear it to
    # pin the guard.
    compiler, entry, f = _fn_body(
        _DELATTR_RECORD + "def drop(b: Bag) -> None:\n    del b.x\n",
        "drop")
    f.body[0].targets[0].dyn_delattr_call = None
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
        if fn is None:
            fold_attempt("body")
    assert fn is None
    assert compiler._thir_fallback == {
        "body:stmt.del_attr:del_attr.unresolved": 1}


def test_if_bool_literal_routes_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(n: Int32) -> Int32:\n"
        "    if True:\n"
        "        n = n + 1\n"
        "    return n\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
    entry = _entry(modules)
    routed = []
    with activate_compiler(compiler):
        for func, self_type in iter_module_callables(entry.ast, entry.analyzer):
            begin_attempt()
            fn = lower_function(func, entry.analyzer, self_type=self_type)
            if fn is None:
                fold_attempt("body")
            else:
                routed.append(fn.name)
    assert compiler._thir_fallback == {}
    assert "rejected" in routed
    assert "clean" in routed


def test_assign_lowering_reject_falls_back_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "class R:\n"
        "    xs: list[Int32]\n"
        "    def __init__(self, xs: list[Int32]):\n"
        "        self.xs = xs\n"
        "def rejected(r: R, r2: R) -> None:\n"
        "    r.xs = r2.xs\n"
    )
    entry = _entry(modules)
    with activate_compiler(compiler):
        func, self_type = next(iter_module_callables(entry.ast, entry.analyzer))
        begin_attempt()
        fn = lower_function(func, entry.analyzer, self_type=self_type)
        if fn is None:
            fold_attempt("body")
    assert fn is None
    assert compiler._thir_fallback == {
        "body:stmt.assign:assign.field_write_shape": 1,
    }


def test_return_lowering_reject_falls_back_at_sync_boundary():
    compiler, modules = _compile(
        "def rejected(s: str) -> str | None:\n"
        "    return s\n"
    )
    entry = _entry(modules)
    with activate_compiler(compiler):
        func, self_type = next(iter_module_callables(entry.ast, entry.analyzer))
        begin_attempt()
        fn = lower_function(func, entry.analyzer, self_type=self_type)
        if fn is None:
            fold_attempt("body")
    assert fn is None
    assert compiler._thir_fallback == {
        "body:stmt.return:return.opt_view_source": 1,
    }


def test_sync_lowering_reports_first_reject_in_source_order():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(s: str) -> str:\n"
        "    assert True\n"
        "    x = (y := s + \"!\")\n"
        "    return x\n"
    )
    entry = _entry(modules)
    with activate_compiler(compiler):
        func, self_type = next(iter_module_callables(entry.ast, entry.analyzer))
        begin_attempt()
        fn = lower_function(func, entry.analyzer, self_type=self_type)
        if fn is None:
            fold_attempt("body")
    assert fn is None
    assert compiler._thir_fallback == {"body:expr.walrus": 1}


def test_constructor_lowering_reports_first_reject_in_source_order():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "class R:\n"
        "    n: str\n"
        "    def __init__(self, n: str):\n"
        "        assert True\n"
        "        x = (y := n + \"!\")\n"
        "        self.n = x\n"
    )
    entry = _entry(modules)
    with activate_compiler(compiler):
        record, init, self_type = next(
            iter_module_constructors(entry.ast, entry.analyzer))
        begin_attempt()
        ctor = lower_constructor(
            record, init, entry.analyzer, self_type=self_type)
        if ctor is None:
            fold_attempt("ctor")
    assert ctor is None
    assert compiler._thir_fallback == {"ctor:expr.walrus": 1}


def test_detail_composes_into_stmt_tag():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def g(n: Int32) -> None:\n"
        "    print(n)\n"
    )
    entry = _entry(modules)
    g = entry.ast.functions[0]
    stmt = g.body[0]
    with activate_compiler(compiler):
        begin_attempt()
        begin_stmt()
        assert note_detail("call.imported_symbol") is False
        # Set-if-empty: a later detail loses to the first.
        assert note_detail("call.arg_shape") is False
        assert stmt_reject_reason(stmt) == "stmt.expr_stmt:call.imported_symbol"
        # A fresh statement clears the slot: bare shape again.
        begin_stmt()
        assert stmt_reject_reason(stmt) == "stmt.expr_stmt"
        assert stmt_reject_reason(
            stmt, "name.global_read") == "stmt.expr_stmt:name.global_read"
        # A landmark tag stands alone -- no detail suffix.
        note_detail("call.imported_symbol")
        comp_stmt = _compile(
            "from tpy import Int32\n"
            "def h(n: Int32) -> None:\n"
            "    d = {i: i for i in range(n)}\n"
        )[1]
    entry2 = _entry(comp_stmt)
    h = entry2.ast.functions[0]
    with activate_compiler(compiler):
        assert stmt_reject_reason(h.body[0]) == "expr.dict_comp"


def test_detail_noop_without_active_compiler():
    token = _current_compiler.set(None)
    try:
        begin_stmt()
        assert note_detail("call.linkage") is False
    finally:
        _current_compiler.reset(token)


def test_classify_stmt_tags():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def g(n: Int32) -> None:\n"
        "    d = {i: i for i in range(n)}\n"
        "    print(n)\n"
    )
    entry = _entry(modules)
    g = entry.ast.functions[0]
    assert classify_stmt(g.body[0]) == "expr.dict_comp"
    assert classify_stmt(g.body[1]) == "stmt.expr_stmt"


def test_type_family_tag_on_signature_types():
    # The shared drilldown family chain, pinned per family so a tag-chain
    # regression (wrong label, broken Own recursion) fails loudly.
    compiler, modules = _compile(
        "from tpy import Int32, Own, Ptr\n"
        "def f(a: Int32, b: Int32 | None, c: list[Int32],\n"
        "      d: tuple[Int32, str], e: Ptr[Int32], g: str,\n"
        "      i: Own[list[Int32]]) -> None:\n"
        "    pass\n"
    )
    entry = _entry(modules)
    an = entry.analyzer
    func = next(fn for fn in entry.ast.functions if fn.name == "f")
    tags = {name: _type_family_tag(pt, an) for name, pt in func.params}
    assert tags == {
        "a": "scalar", "b": "optional", "c": "container", "d": "tuple",
        "e": "ptr", "g": "str", "i": "own_container",
    }
    assert _type_family_tag(None, an) == "untyped"


def test_iterator_object_local_as_protocol_arg_byte_identical():
    # An iterator-object local forwarded into a protocol-typed param -- the
    # decl.iterator_object + protocol-arg interaction (routes today; pinned
    # as a byte-identity regression guard).
    from .testutil import _assert_byte_identical
    _assert_byte_identical(
        "from tpy import Int32\n"
        "from typing import Iterator, Iterable\n"
        "def gen(n: Int32) -> Iterator[Int32]:\n"
        "    yield n\n"
        "def total(it: Iterable[Int32]) -> Int32:\n"
        "    t = 0\n"
        "    for v in it:\n"
        "        t = t + v\n"
        "    return t\n"
        "def use(n: Int32) -> Int32:\n"
        "    it = gen(n)\n"
        "    return total(it)\n")


def test_reassigned_iterator_object_local_falls_back():
    # The iterator-object decl gate excludes reassigned names -- a rebind
    # would need pointer machinery the arm does not carry.
    compiler, entry, f = _fn_body(
        "from tpy import Int32\n"
        "from typing import Iterator\n"
        "def gen(n: Int32) -> Iterator[Int32]:\n"
        "    yield n\n"
        "def rejected(n: Int32) -> Int32:\n"
        "    it = gen(n)\n"
        "    it = gen(n + 1)\n"
        "    t = 0\n"
        "    for v in it:\n"
        "        t = t + v\n"
        "    return t\n",
        "rejected")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
        if fn is None:
            fold_attempt("body")
    assert fn is None


def test_branch_first_iterator_object_decl_falls_back():
    # `iterator_object_locals` is outside the branch snapshot -- a
    # branch-first iterator decl must reject (function scope only), so the
    # registration can never leak past its branch.
    compiler, entry, f = _fn_body(
        "from tpy import Int32\n"
        "from typing import Iterator\n"
        "def gen(n: Int32) -> Iterator[Int32]:\n"
        "    yield n\n"
        "def rejected(c: bool, n: Int32) -> Int32:\n"
        "    t = 0\n"
        "    if c:\n"
        "        it = gen(n)\n"
        "        for v in it:\n"
        "            t = t + v\n"
        "    return t\n",
        "rejected")
    with activate_compiler(compiler):
        begin_attempt()
        fn = lower_function(f, entry.analyzer, self_type=None)
        if fn is None:
            fold_attempt("body")
    assert fn is None


def _reasons(src: str) -> dict:
    from ..codegen_cpp import CodeGenOptions
    compiler, modules = _compile(src)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


def test_native_arg_container_is_not_labelled_a_record():
    # Containers are NominalType, so without a guard they fall into the record
    # split and a whole family reads as blocked on the non-F1-record frontier.
    # A DOUBLY nested element (`m[0][1]`) now resolves one receiver level
    # deeper (the double-subscript receiver arm), so the reject moved to the
    # inner subscript's own receiver frontier -- still a rejected container
    # shape, never the record label.
    src = ("from tpy import Int32\n"
           "def f(m: list[list[list[Int32]]]) -> None:\n"
           "    print(len(m[0][1]))\n"
           "def main() -> None:\n    pass\nmain()\n")
    reasons = _reasons(src)
    assert any(k.endswith("call.native_arg.container")
               or k.endswith("subscript.recv.subscript")
               for k in reasons), reasons
    assert not any("record_nonf1" in k for k in reasons), reasons


def test_print_arg_reports_the_inner_reject_not_its_own_shape():
    # The print arm's own shape tag is composed AFTER the inner lowering
    # rejects; since the detail slot is first-wins, a tag composed here would
    # win by default and bury the reason that actually blocked the body.
    # The inner shape must still reject: a RECORD-name needle over a LIST
    # haystack rides the ranges_contains arm, whose needle set is
    # scalar/str/ptr-tuple only. (The previous fixture -- a view-keyed set
    # membership -- routed when the view-key family landed.)
    src = ("from tpy import Int32\n"
           "class Point:\n"
           "    x: Int32\n"
           "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
           "    def __eq__(self, other: Point) -> bool:\n"
           "        return self.x == other.x\n"
           "class Item:\n"
           "    pts: list[Point]\n"
           "    def __init__(self) -> None:\n        self.pts = []\n"
           "def f(item: Item, p: Point) -> None:\n"
           "    print(p in item.pts)\n"
           "def main() -> None:\n    pass\nmain()\n")
    reasons = _reasons(src)
    assert any("binop.shape.in" in k for k in reasons), reasons
    assert not any("print.arg." in k for k in reasons), reasons
