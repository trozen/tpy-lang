"""The storage-optional comp/genexpr unpack mirror wave.

`lc.storage_opt_locals` mirrors codegen's `storage_form_optional_locals`
for comp/genexpr unpack targets binding a ptr-repr Optional[F1-record]
tuple element (`auto& p = std::get<0>(__tup_N);` -- storage form, not
pointer-accessed). Reads render the bare storage optional; a `T*` arg
slot lifts via `::tpy::optional_to_ptr(p)`. The genexpr grew unpack
heads (per-target binds off `auto& __tup_N = *__beg++;` in the
make_generator lambda).
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _lower_ctx_witnessed,
)


def _gen(source):
    compiler, modules = _compile(source)
    entry = _entry(modules)
    outs = {}
    for flag in (False, True):
        outs[flag] = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          comment_line_numbers=False,
                                          thir_codegen=flag))
    return compiler, outs[False], outs[True]


_PRE = (
    "from tpy import Int32\n"
    "class P:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
    "def borrow(p: P | None) -> Int32:\n"
    "    if p is None:\n"
    "        return Int32(-1)\n"
    "    return p.x\n"
)


class TestCompAndGenexprUnpackOptional:
    # The witness shape: comp unpack binds the storage optional element by
    # reference and the consumer lifts at the `P*` slot; the genexpr twin
    # binds the same lines inside the make_generator lambda.
    SRC = (
        _PRE +
        "def main() -> None:\n"
        "    items: list[tuple[P | None, Int32]] = [\n"
        "        (P(Int32(1)), Int32(10)),\n"
        "        (None, Int32(20)),\n"
        "    ]\n"
        "    results = [borrow(p) for p, n in items]\n"
        "    for r in results:\n"
        "        print(r)\n"
        "    total = sum(n for p, n in items)\n"
        "    print(total)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        assert not dict(compiler._thir_fallback), dict(compiler._thir_fallback)
        assert "auto& p = std::get<0>(__tup_1);" in thir[1]
        assert "borrow(::tpy::optional_to_ptr(p))" in thir[1]
        assert "auto& __tup_2 = *__beg++;" in thir[1]
        w = compiler._thir_face_witnesses
        assert w.get("name.storage_opt_whole", 0) >= 1
        assert w.get("optptr.storage_name_lift", 0) >= 1
        assert w.get("genexpr.unpack", 0) >= 1


class TestForStatementHeadRoutesOptPtr:
    # The for-STATEMENT head over Optional-element tuples now routes: the
    # opt_ptr target binds `T* p = std::get<0>(__tup_N);` off the lifted
    # head (the standalone unpack's bind at the for head).
    SRC = (
        _PRE +
        "def main() -> None:\n"
        "    items: list[tuple[P | None, Int32]] = [(P(Int32(1)), Int32(10))]\n"
        "    total = 0\n"
        "    for p, n in items:\n"
        "        total = total + n\n"
        "    print(total)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        fb = dict(compiler._thir_fallback)
        assert not any(k.startswith("body:") for k in fb), fb
        w = compiler._thir_face_witnesses
        assert w.get("stmt.tuple_unpack.opt_ptr_target", 0) >= 1


class TestNarrowedTargetReadRoutes:
    # CONVERTED (storage-opt narrow wave): a NARROWED occurrence of the
    # registered unpack target derefs at the access -- `(*p).x` -- through
    # the same rows the storage-opt loop var rides.
    SRC = (
        _PRE +
        "def main() -> None:\n"
        "    items: list[tuple[P | None, Int32]] = [(P(Int32(1)), Int32(10))]\n"
        "    vals = [(p.x if p is not None else n) for p, n in items]\n"
        "    print(vals[0])\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        from .testutil import _assert_routes_byte_identical
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "(*p).x" in cpp


class TestOwnedStrGenexprUnpackTarget:
    """An owned-`str` unpack target: the AST's is_value_type branch spells
    the tuple's OWNED element type (`std::string k = std::get<0>(t);`), so
    the target's reads stay storage-form and bare-insert at the dict slot.
    The comp unpack head already carried this disjunct; the genexpr head
    was missing it. Corpus witness: tuple/tuple_literal_list."""

    SRC = (
        "from tpy import Int32\n"
        "def main() -> None:\n"
        "    raw = [('x', 10), ('y', 20)]\n"
        "    d = dict[str, Int32]((k, v) for k, v in raw)\n"
        "    names = list[str](k for k, v in raw)\n"
        "    tot = sum(Int32(len(k)) + v for k, v in raw)\n"
        "    flip = dict[Int32, str]((v, k) for k, v in raw)\n"
        "    keep = dict[str, Int32]((k, v) for k, v in raw if v > 10)\n"
        "    print(d['x'], names[0], tot, flip[10], len(keep))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _thir, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("genexpr.unpack", 0) >= 5
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "std::string k = std::get<0>(" in cpp


class TestViewStrGenexprUnpackTargetDefers:
    # BOUNDARY: a StrView-typed element makes the container hold VIEWS,
    # whose literal-key/element renders the owned-str slot predicate does
    # not reproduce -- the target keeps the named reject.
    SRC = (
        "from tpy import Int32, StrView\n"
        "def take(vs: list[tuple[StrView, Int32]]) -> Int32:\n"
        "    return sum(Int32(len(k)) + v for k, v in vs)\n"
        "def main() -> None:\n"
        "    vs: list[tuple[StrView, Int32]] = []\n"
        "    vs.append((StrView('p'), 1))\n"
        "    print(take(vs))\n"
        "main()\n"
    )

    def test_defers_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        fb = dict(compiler._thir_fallback)
        assert fb.get("body:genexpr.unpack") == 1, fb


class TestMovedGenexprUnpackDefers:
    # BOUNDARY: a moved container-LITERAL source with an unpack head is
    # unwitnessed and keeps the named reject (the unpack route admits
    # lvalue sources only).
    SRC = (
        "from tpy import Int32\n"
        "class P:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "def main() -> None:\n"
        "    total = sum(n for p, n in [(P(Int32(1)), Int32(10))])\n"
        "    print(total)\n"
        "main()\n"
    )

    def test_defers_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        fb = dict(compiler._thir_fallback)
        assert fb.get("body:genexpr.unpack") == 1, fb


class TestFilteredGenexprUnpackRoutes:
    # A filtered UNPACK genexpr composes the C4 filter cell with the unpack
    # head (`if (n > 0) { return ...; }` inside the lambda; former fence,
    # converted when the filter cell landed) -- routing + byte identity.
    SRC = (
        _PRE +
        "def main() -> None:\n"
        "    items: list[tuple[P | None, Int32]] = [(P(Int32(1)), Int32(10))]\n"
        "    total = sum(n for p, n in items if n > 0)\n"
        "    print(total)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        fb = dict(compiler._thir_fallback)
        assert not fb, fb


class TestValueOptAssignTarget:
    """The registered value-opt NAME assign-target row: a raw TpyAssign
    target is a whole-BINDING write, lowered with allow_whole_optional
    (the @model macro's generated `color = __color_2;` -- user code
    reaches the same binding via the var-decl reassign arm, which never
    lowers the target as an expression, so the raw-assign shape only
    arises in generated bodies). The unproven-read fence stays for
    SOURCE positions. No boundary pin for an UNREGISTERED target: the
    parser never emits a raw NAME assign for user source and today's
    macros always target a registered local -- and the failure direction
    is a safe whole-body fallback, which the corpus byte-diff would
    catch if a future macro changed that. Corpus witnesses:
    tplib/json_model_errors and the two panic twins."""

    def test_macro_generated_assign_routes(self):
        src = ("from enum import Enum\n"
               "from tplib.json import JsonError\n"
               "from tplib.json.model import model\n"
               "class Color(Enum):\n"
               "    Red = 0\n"
               "    Blue = 1\n"
               "@model\n"
               "class Item:\n"
               "    name: str\n"
               "    color: Color\n"
               "def main() -> None:\n"
               "    try:\n"
               "        Item.try_from_json('{\"name\": \"x\"}')\n"
               "    except JsonError as e:\n"
               "        print(e.message)\n"
               "main()\n")
        _thir, w = _lower_ctx_witnessed(src)
        assert w.get("assign.value_opt_target", 0) >= 1
        _assert_routes_byte_identical(src)
