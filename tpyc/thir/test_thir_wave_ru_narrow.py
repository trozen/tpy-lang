"""Pins for the isinstance-narrowing cell-A arms -- F6 wrapper-union
subjects (the `.value` holds/get render + the narrowed-alias consumers:
setitem / qualcall arg / subscript read / print), F1 folded conditions on
already-narrowed subjects, F2 readonly-qualified subjects -- and the
boundaries that must keep falling back."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_byte_identical,
    _fn,
    _lower_ctx,
    _lower_ctx_witnessed,
)

_JSON_NARROW = (
    "import json\n"
    "def f(s: str) -> None:\n"
    "    d = json.loads(s)\n"
    "    if isinstance(d, dict):\n"
    "        d[\"b\"] = 2\n"
    "        print(json.dumps(d))\n"
)

_JSON_NESTED = (
    "import json\n"
    "from json import JsonValue\n"
    "def f(s: str) -> None:\n"
    "    d = json.loads(s)\n"
    "    if isinstance(d, dict):\n"
    "        v: JsonValue = d[\"rows\"]\n"
    "        if isinstance(v, int):\n"
    "            print(\"rows =\", v)\n"
)


class TestWrapperUnionNarrow:
    def test_local_subject_routes_witnessed(self):
        thir, faces = _lower_ctx_witnessed(_JSON_NARROW)
        assert _fn(thir, "f") is not None
        assert faces["narrow.wrapper_union"] >= 1
        assert faces["setitem.ru_scalar"] >= 1

    def test_local_subject_byte_identical(self):
        _assert_byte_identical(_JSON_NARROW)

    def test_nested_narrow_routes_witnessed(self):
        # The requests_get shape: wrapper-elem REF_ALIAS decl off the
        # narrowed dict (raw operator[] -- the fi-fallback keyed on the
        # DECLARED union), nested wrapper narrow of the element alias, and
        # the union-typed `::tpy::__str__` print visitor.
        thir, faces = _lower_ctx_witnessed(_JSON_NESTED)
        assert _fn(thir, "f") is not None
        assert faces["subscript.ru_narrowed_recv"] >= 1
        assert faces["print.union_narrowed_arg"] >= 1

    def test_nested_narrow_byte_identical(self):
        _assert_byte_identical(_JSON_NESTED)

    def test_nested_narrow_emits_checked_getitem(self):
        # The narrowed-dict read routes the CHECKED dunder (a missing key
        # raises KeyError), never the raw operator[] that default-inserts.
        from .testutil import _compile, _entry
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(_JSON_NESTED)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False))
        assert '::tpy::__getitem__(__d, "rows")' in cpp
        assert '__d["rows"]' not in cpp

    def test_param_subject_cond_routes(self):
        # A wrapper-union PARAM subject narrows with the `const auto&`
        # extraction alias (read-only body) -- routes byte-identically.
        src = (
            "import json\n"
            "from json import JsonValue\n"
            "def g(v: JsonValue) -> None:\n"
            "    if isinstance(v, str):\n"
            "        print(v)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "g") is not None
        assert faces["narrow.wrapper_union"] >= 1
        _assert_byte_identical(src)

    def test_param_subject_write_stays_ast(self):
        # A param subject's extraction alias is `const auto&`; the AST
        # writes through it (uncompilable C++, BUGS.md) -- the write body
        # must keep falling back, never mirror.
        src = (
            "import json\n"
            "from json import JsonValue\n"
            "def g(v: JsonValue) -> None:\n"
            "    if isinstance(v, dict):\n"
            "        v[\"k\"] = 1\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.assign:setitem.const_narrowed_recv")

    def test_param_source_elem_decl_stays_ast(self):
        # The wrapper-elem REF_ALIAS row is LOCAL-subject only: off a param
        # subject's const alias the AST emits a non-const `T&` bind
        # (uncompilable C++, BUGS.md) -- keep falling back.
        src = (
            "import json\n"
            "from json import JsonValue\n"
            "def g(v: JsonValue) -> None:\n"
            "    if isinstance(v, list):\n"
            "        x: JsonValue = v[0]\n"
            "        print(json.dumps(x))\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.branch_slot_type")

    def test_compound_cond_rejects(self):
        # The branch-entry extraction spells the wrapper `.value` fine, but
        # the isinstance CONDITION over a wrapper subject has no truthy
        # render inside an `and` chain, so the body rejects there.
        src = (
            "import json\n"
            "def f(s: str, flag: bool) -> None:\n"
            "    d = json.loads(s)\n"
            "    if isinstance(d, dict) and flag:\n"
            "        print(json.dumps(d))\n"
        )
        _assert_rejects_at(
            _reject_tally(src), "body:stmt.if",
            "if.cond_binop.&&.scalar_scalar:truthy.call_nonbool")

    def test_foreach_over_narrowed_alias_routes(self):
        # gen_expr's for-dispatch keys the DECLARED union, so a narrowed-alias
        # iterable renders the generic `__iter__` protocol loop over the alias
        # -- never the member's begin/end peephole. That IS the iter_proto
        # route, so lowering now selects it rather than rejecting; the render
        # is unchanged (corpus-caught on union_recursive_nested_literal's
        # json_keys, which this pin was written from).
        src = (
            "import json\n"
            "def f(s: str) -> None:\n"
            "    n = 0\n"
            "    d = json.loads(s)\n"
            "    if isinstance(d, dict):\n"
            "        for k in d:\n"
            "            n = n + 1\n"
            "    print(n)\n"
        )
        _assert_byte_identical(src)
        assert _fn(_lower_ctx(src), "f") is not None

    def test_setitem_none_value_stays_ast(self):
        # None into a wrapper-union value slot (the monostate spelling) is
        # unwitnessed -- the write body keeps falling back.
        src = (
            "import json\n"
            "def f(s: str) -> None:\n"
            "    d = json.loads(s)\n"
            "    if isinstance(d, dict):\n"
            "        d[\"b\"] = None\n"
            "        print(json.dumps(d))\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.assign:setitem.ru_value_shape")


_AB = (
    "class A:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
    "class B:\n"
    "    y: Int32\n"
    "    def __init__(self, y: Int32) -> None:\n"
    "        self.y = y\n"
)

_PRELUDE = "from tpy import Int32, readonly\n"


class TestFoldedNarrow:
    def test_redundant_fold_routes_witnessed(self):
        # `if (true)` + the SHADOWING re-extraction from the ORIGINAL union
        # (never the live alias).
        src = _PRELUDE + _AB + (
            "def f(v: A | B) -> Int32:\n"
            "    if isinstance(v, A):\n"
            "        if isinstance(v, A):\n"
            "            return v.x\n"
            "    return -1\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces["narrow.folded_isinstance"] >= 1
        _assert_byte_identical(src)

    def test_dead_branch_fold_extracts_checked_member(self):
        # `if (false)` -- the dead branch's alias extracts the CHECKED
        # member (B), not the live narrowed one (A).
        src = _PRELUDE + _AB + (
            "def f(v: A | B) -> Int32:\n"
            "    if isinstance(v, A):\n"
            "        if isinstance(v, B):\n"
            "            return v.y\n"
            "        return v.x\n"
            "    return -1\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces["narrow.folded_isinstance"] >= 1
        _assert_byte_identical(src)

    def test_fold_with_else_stays_ast(self):
        # A folded condition with an explicit else is unwitnessed (the
        # AST's dead-else suppression is a different render family).
        src = _PRELUDE + _AB + (
            "def f(v: A | B) -> Int32:\n"
            "    if isinstance(v, A):\n"
            "        if isinstance(v, A):\n"
            "            return v.x\n"
            "        else:\n"
            "            return -9\n"
            "    return -1\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.if:if.folded_narrow_shape")


class TestReadonlySubject:
    def test_readonly_ptr_union_param_routes(self):
        # F2: the readonly callable's union param (`Ref(ReadonlyType(U))`)
        # narrows with const-qualified alternatives
        # (`std::get<const Dog*>`).
        src = _PRELUDE + _AB + (
            "@readonly\n"
            "def f(v: A | B) -> Int32:\n"
            "    if isinstance(v, A):\n"
            "        return v.x\n"
            "    return -1\n"
        )
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_readonly_wrapper_param_routes(self):
        src = (
            "import json\n"
            "from json import JsonValue\n"
            "from tpy import readonly\n"
            "@readonly\n"
            "def g(v: JsonValue) -> str:\n"
            "    if isinstance(v, str):\n"
            "        return v\n"
            "    return \"\"\n"
        )
        assert _fn(_lower_ctx(src), "g") is not None
        _assert_byte_identical(src)
