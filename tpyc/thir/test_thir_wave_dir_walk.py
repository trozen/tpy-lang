"""Four admission rows a directory-walk generator needs at once, each with
its own rule: an `Optional[Callable]` frame param, a container FIELD read at
a container instantiation, a str FIELD element in a borrow-form yield tuple,
and a one-source container instantiation at an `Own[container]` ctor slot.
Corpus witness: os.walk.
"""

from __future__ import annotations

import pytest

from .testutil import (
    _assert_byte_identical, _assert_rejects_at,
    _assert_routes_byte_identical, _compile, _entry,
)
from ..codegen_cpp import CodeGenOptions
from ..diagnostics import SemanticError


def _emit(src: str):
    compiler, modules = _compile(src)
    hpp, cpp = compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                thir_codegen=True))
    return compiler, hpp, cpp


_REC = (
    "from typing import Callable, Iterator\n"
    "from tpy import Int32, Own\n"
    "class Rec:\n"
    "    label: str\n"
    "    rows: list[str]\n"
    "    opt_rows: list[str] | None\n"
    "    def __init__(self, label: str) -> None:\n"
    "        self.label = label\n"
    "        self.rows = []\n"
    "        self.opt_rows = None\n"
)


class TestOptionalCallableFrameParam:
    """An `Optional[Callable]` param on a RESUMABLE frame. The capture is the
    same moved `std::optional<std::function<..>>` field the non-optional
    callable param already takes, with the optional's own read arms on top --
    so the two share one admission rather than the optional getting a
    coro-specific form.
    """

    SRC = (_REC
           + "def rows(r: Rec, n: Int32,\n"
           + "         onerror: Callable[[Int32], None] | None = None\n"
           + "         ) -> Iterator[tuple[str, list[str]]]:\n"
           + "    i = 0\n"
           + "    while i < n:\n"
           + "        out: list[str] = []\n"
           + "        if onerror is not None:\n"
           + "            onerror(i)\n"
           + "        yield (r.label, out)\n"
           + "        i += 1\n")

    def test_frame_param_routes(self):
        _assert_routes_byte_identical(self.SRC)
        _, hpp, _ = _emit(self.SRC)
        assert ("    std::optional<std::function<void(int32_t)>> onerror;"
                in hpp)
        assert "onerror(std::move(onerror_))" in hpp

    def test_narrowed_call_reads_through_the_optional(self):
        _, _, cpp = _emit(self.SRC)
        assert "if ((onerror.has_value())) {" in cpp
        assert "onerror.value()(i);" in cpp

    def test_template_callable_cannot_be_optional(self):
        # The adjacent shape this admission would have to exclude -- an `Fn`
        # TEMPLATE slot, which has no value binding and so no `std::optional`
        # field -- is not expressible: sema refuses `Fn` nested in Optional.
        # If that ever becomes legal, the frame-param gate owes it a decision
        # rather than the inherited yes, and this pin is what says so.
        src = (_REC
               + "from tpy import Fn\n"
               + "def rows2(n: Int32, cb: Fn[[Int32], None] | None = None\n"
               + "          ) -> Iterator[Int32]:\n"
               + "    i = 0\n"
               + "    while i < n:\n"
               + "        out: list[Int32] = []\n"
               + "        out.append(i)\n"
               + "        yield i\n"
               + "        i += 1\n")
        with pytest.raises(SemanticError):
            _compile(src)


class TestStrFieldInBorrowYieldTuple:
    """A str/bytes FIELD read at a VALUE-mode element of a BORROW-form tuple.
    The element renders exactly as the value-tuple builder's does -- the bare
    member read plus the form-keyed owned wrap -- so the field source rides
    the same admission there. A position that does NOT consume the read whole
    (a container-literal element) keeps rejecting.
    """

    def test_str_field_element_routes(self):
        src = TestOptionalCallableFrameParam.SRC
        _assert_routes_byte_identical(src)
        _, _, cpp = _emit(src)
        assert ("return std::tuple<std::string, std::vector<std::string>*>"
                "{r.label, &((*out))};" in cpp)

    def test_container_literal_element_stays_ast(self):
        # BOUNDARY (dualgen-probed): the container-literal element does not
        # consume the read whole, so the str-field admission must not reach
        # it.
        src = (_REC
               + "def lit(r: Rec) -> Int32:\n"
               + "    xs = [r.label]\n"
               + "    return Int32(len(xs))\n")
        compiler, _, _ = _emit(src)
        _assert_rejects_at(compiler._thir_fallback, "body:stmt.var_decl",
                           "field.result_type")
        _assert_byte_identical(src)


class TestContainerFieldAtInstantiation:
    """A container FIELD read at a container instantiation arg
    (`list(item.dirnames)`): the member render binds the construct template
    bare. No move leg -- the AST's last-use movability is keyed on a NAME, so
    a field read is never a move source. DECLARED-type keyed, so a narrowed
    `Optional[list]` field (whose AST render unwraps) stays out.
    """

    def test_field_source_routes(self):
        src = (_REC
               + "def copy_rows(r: Rec) -> Int32:\n"
               + "    a = list(r.rows)\n"
               + "    return Int32(len(a))\n")
        _assert_routes_byte_identical(src)
        compiler, _, cpp = _emit(src)
        assert compiler._thir_face_witnesses.get("call.inst_field_arg", 0) >= 1
        assert ("::tpy::construct<std::vector<std::string>>(r.rows)" in cpp)

    def test_narrowed_optional_field_stays_ast(self):
        # BOUNDARY (dualgen-probed): the narrowed read types as a plain
        # container but renders through the unwrap, so it is not the bare
        # member read this row admits.
        src = (_REC
               + "def copy_opt(r: Rec) -> Int32:\n"
               + "    if r.opt_rows is not None:\n"
               + "        a = list(r.opt_rows)\n"
               + "        return Int32(len(a))\n"
               + "    return 0\n")
        compiler, _, _ = _emit(src)
        _assert_rejects_at(compiler._thir_fallback, "body:expr.call",
                           "call.inst_arg_shape")
        _assert_byte_identical(src)


class TestOwnContainerConstructCtorArg:
    """A one-source container instantiation at an `Own[container]` ctor slot
    (`_WalkEmit(cur, list(names), files)`): the construct template IS the
    whole render and needs no temp, so the owning slot binds the prvalue
    directly. A BORROW container slot is a different question -- the temp's
    lifetime -- and keeps rejecting.
    """

    _BAG = (_REC
            + "class Bag:\n"
            + "    names: list[str]\n"
            + "    def __init__(self, names: Own[list[str]]) -> None:\n"
            + "        self.names = names\n")

    def test_own_slot_routes(self):
        src = (self._BAG
               + "def build(names: list[str]) -> Own[Bag]:\n"
               + "    return Bag(list(names))\n"
               + "def build_nested(names: list[str], out: list[Bag]) -> None:\n"
               + "    out.append(Bag(list(names)))\n")
        _assert_routes_byte_identical(src)
        compiler, _, cpp = _emit(src)
        assert compiler._thir_face_witnesses.get(
            "ctor.own_container_construct", 0) >= 1
        assert ("return Bag(::tpy::construct<std::vector<std::string>>"
                "(names));" in cpp)
        assert ("out.push_back(Bag(::tpy::construct<std::vector<std::string>>"
                "(names)));" in cpp)

    def test_borrow_container_slot_stays_ast(self):
        # BOUNDARY (dualgen-probed): a by-reference container slot binds a
        # materialized temp rather than owning the prvalue, so it is not this
        # row's render and must be decided separately.
        src = (_REC
               + "class RefBag:\n"
               + "    n: Int32\n"
               + "    def __init__(self, names: list[str]) -> None:\n"
               + "        self.n = Int32(len(names))\n"
               + "def build_ref(names: list[str]) -> Own[RefBag]:\n"
               + "    return RefBag(list(names))\n")
        compiler, _, _ = _emit(src)
        _assert_rejects_at(compiler._thir_fallback, "body:expr.call",
                           "call.ctor_arg.container")
        _assert_byte_identical(src)
