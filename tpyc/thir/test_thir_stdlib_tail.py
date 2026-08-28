"""Stdlib-tail admission rows: the `Ptr[T]` RESULT of a structural-protocol
method. Corpus witness: tpy/sync's `Condvar.wait` (`lock._raw_mutex()`)."""

from .testutil import (_assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _ctor_tail,
                       _entry, _lower_ctor, _lower_ctx_witnessed)
from ..codegen_cpp import CodeGenOptions


_NODE = ("from tpy import Int32, Ptr, take_ptr, readonly\n"
         "from typing import Protocol\n"
         "class Node:\n"
         "    v: Int32\n"
         "    def __init__(self) -> None:\n        self.v = 1\n")


def _fallback(src: str):
    compiler, modules = _compile(src)
    compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                comment_line_numbers=False,
                                                thir_codegen=True))
    return dict(compiler._thir_fallback)


class TestProtocolPtrResult:
    ARG_SLOT = (_NODE
                + "class HasPtr(Protocol):\n"
                + "    def raw(self) -> Ptr[Node]: ...\n"
                + "class Holder:\n"
                + "    n: Node\n"
                + "    def __init__(self) -> None:\n        self.n = Node()\n"
                + "    def raw(self) -> Ptr[Node]:\n"
                + "        return take_ptr(self.n)\n"
                + "def sink(p: Ptr[Node]) -> Int32:\n"
                + "    return p.__deref__().v\n"
                + "def use(h: HasPtr) -> Int32:\n"
                + "    return sink(h.raw())\n"
                + "def main() -> None:\n"
                + "    h = Holder()\n"
                + "    print(use(h))\n"
                + "main()\n")

    def test_arg_slot_routes_byte_identical(self):
        _, witnesses = _lower_ctx_witnessed(self.ARG_SLOT)
        assert witnesses.get("method.protocol_ptr_ret", 0) >= 1
        _assert_routes_byte_identical(self.ARG_SLOT)

    def test_decl_slot_routes_byte_identical(self):
        # The same result landing in a pointer LOCAL rather than an arg slot.
        src = (_NODE
               + "class HasPtr(Protocol):\n"
               + "    def raw(self) -> Ptr[Node]: ...\n"
               + "class Holder:\n"
               + "    n: Node\n"
               + "    def __init__(self) -> None:\n        self.n = Node()\n"
               + "    def raw(self) -> Ptr[Node]:\n"
               + "        return take_ptr(self.n)\n"
               + "def use(h: HasPtr) -> Int32:\n"
               + "    p = h.raw()\n"
               + "    return p.__deref__().v\n"
               + "def main() -> None:\n"
               + "    print(use(Holder()))\n"
               + "main()\n")
        _, witnesses = _lower_ctx_witnessed(src)
        assert witnesses.get("method.protocol_ptr_ret", 0) >= 1
        _assert_routes_byte_identical(src)

    def test_scalar_pointee_routes_byte_identical(self):
        src = (_NODE
               + "class HasPtr(Protocol):\n"
               + "    def raw(self) -> Ptr[Int32]: ...\n"
               + "class Holder:\n"
               + "    x: Int32\n"
               + "    def __init__(self) -> None:\n        self.x = 5\n"
               + "    def raw(self) -> Ptr[Int32]:\n"
               + "        return take_ptr(self.x)\n"
               + "def sink(p: Ptr[Int32]) -> Int32:\n"
               + "    return p.__deref__()\n"
               + "def use(h: HasPtr) -> Int32:\n"
               + "    return sink(h.raw())\n"
               + "def main() -> None:\n"
               + "    print(use(Holder()))\n"
               + "main()\n")
        _, witnesses = _lower_ctx_witnessed(src)
        assert witnesses.get("method.protocol_ptr_ret", 0) >= 1
        _assert_routes_byte_identical(src)

    def test_readonly_pointee_routes_byte_identical(self):
        # `Ptr[readonly[T]]` -> `const T*`: the const flavour of the same row.
        src = (_NODE
               + "class HasPtr(Protocol):\n"
               + "    def raw(self) -> Ptr[readonly[Node]]: ...\n"
               + "class Holder:\n"
               + "    n: Node\n"
               + "    def __init__(self) -> None:\n        self.n = Node()\n"
               + "    def raw(self) -> Ptr[readonly[Node]]:\n"
               + "        return take_ptr(self.n)\n"
               + "def sink(p: Ptr[readonly[Node]]) -> Int32:\n"
               + "    return p.__deref__().v\n"
               + "def use(h: HasPtr) -> Int32:\n"
               + "    return sink(h.raw())\n"
               + "def main() -> None:\n"
               + "    print(use(Holder()))\n"
               + "main()\n")
        _, witnesses = _lower_ctx_witnessed(src)
        assert witnesses.get("method.protocol_ptr_ret", 0) >= 1
        _assert_routes_byte_identical(src)

    def test_dynamic_protocol_receiver_routes_byte_identical(self):
        # The vtable-dispatch twin of the monomorphized receiver.
        src = ("from tpy import Int32, Ptr, take_ptr, dynamic\n"
               + "from typing import Protocol\n"
               + "class Node:\n"
               + "    v: Int32\n"
               + "    def __init__(self) -> None:\n        self.v = 1\n"
               + "@dynamic\n"
               + "class HasPtr(Protocol):\n"
               + "    def raw(self) -> Ptr[Node]: ...\n"
               + "class Holder:\n"
               + "    n: Node\n"
               + "    def __init__(self) -> None:\n        self.n = Node()\n"
               + "    def raw(self) -> Ptr[Node]:\n"
               + "        return take_ptr(self.n)\n"
               + "def sink(p: Ptr[Node]) -> Int32:\n"
               + "    return p.__deref__().v\n"
               + "def use(h: HasPtr) -> Int32:\n"
               + "    return sink(h.raw())\n"
               + "def main() -> None:\n"
               + "    print(use(Holder()))\n"
               + "main()\n")
        _, witnesses = _lower_ctx_witnessed(src)
        assert witnesses.get("method.protocol_ptr_ret", 0) >= 1
        _assert_routes_byte_identical(src)

    CONTAINER_POINTEE = (
        "from tpy import Int32, Ptr, take_ptr\n"
        "from typing import Protocol\n"
        "class HasPtr(Protocol):\n"
        "    def raw(self) -> Ptr[list[Int32]]: ...\n"
        "class Holder:\n"
        "    xs: list[Int32]\n"
        "    def __init__(self) -> None:\n        self.xs = [1, 2]\n"
        "    def raw(self) -> Ptr[list[Int32]]:\n"
        "        return take_ptr(self.xs)\n"
        "def use(h: HasPtr) -> Int32:\n"
        "    p = h.raw()\n"
        "    return 0\n"
        "def main() -> None:\n"
        "    print(use(Holder()))\n"
        "main()\n")

    def test_container_pointee_stays_ast(self):
        # A pointee the value-Ptr slice excludes (no F1-record / scalar
        # spelling) must never reach a routed body. The composing decl slot
        # is what decides here, ahead of the result set -- so widening the
        # pointee rule alone would still not route this shape.
        fallback = _fallback(self.CONTAINER_POINTEE)
        _assert_rejects_at(fallback, "body:stmt.var_decl",
                           shape="decl.slot_type")
        _assert_byte_identical(self.CONTAINER_POINTEE)


_MO = ("from tpy import Int32\n"
       "from tpy.extern import native, cpp_template\n"
       "from enum import Enum\n"
       "@native('std::memory_order')\n"
       "class MO(Enum):\n"
       "    RELAXED = 0\n"
       "    SEQ_CST = 5\n")


class TestPtrTemplateEnumAndTparamArgs:
    """The ptr / @native-record template family's arg + result rows.
    Corpus witness: tpy/atomic's `Atomic.compare_exchange{,_weak}`."""

    CAS = (_MO
           + "@native('tpy::MovableAtomic')\n"
           + "class Raw[T]:\n"
           + "    def __init__(self, value: T) -> None: ...\n"
           + "    @native('tpy::atomic_cas', function=True)\n"
           + "    def cas(self, expected: T, desired: T, s: MO,\n"
           + "            f: MO) -> tuple[bool, T]: ...\n"
           + "class At[T]:\n"
           + "    _raw: Raw[T]\n"
           + "    def __init__(self, value: T) -> None:\n"
           + "        self._raw = Raw[T](value)\n"
           + "    def cas(self, expected: T, desired: T, s: MO = MO.SEQ_CST,\n"
           + "            f: MO = MO.SEQ_CST) -> tuple[bool, T]:\n"
           + "        return self._raw.cas(expected, desired, s, f)\n"
           + "def main() -> None:\n"
           + "    a = At[Int32](1)\n"
           + "    ok, obs = a.cas(1, 2)\n"
           + "    print(ok)\n"
           + "main()\n")

    def test_cas_routes_byte_identical(self):
        _, witnesses = _lower_ctx_witnessed(self.CAS)
        assert witnesses.get("method.ptr_template_enum_arg", 0) >= 1
        assert witnesses.get("method.ptr_template_tparam_arg", 0) >= 1
        assert witnesses.get("method.ptr_template_open_t_tuple_ret", 0) >= 1
        out = _assert_routes_byte_identical(self.CAS)
        assert ("return ::tpy::atomic_cas(this->_raw, expected, desired, "
                "s, f);") in "".join(out)

    def test_template_enum_arg_routes_byte_identical(self):
        # The @cpp_template half of the same family: the enum member
        # interpolates into the positional slot.
        src = (_MO
               + "@native('tpy::Widget')\n"
               + "class W:\n"
               + "    def __init__(self) -> None: ...\n"
               + "    @cpp_template('{self}.tune({0})')\n"
               + "    def tune(self, m: MO) -> Int32: ...\n"
               + "def main() -> None:\n"
               + "    w = W()\n"
               + "    print(w.tune(MO.RELAXED))\n"
               + "main()\n")
        _, witnesses = _lower_ctx_witnessed(src)
        assert witnesses.get("method.ptr_template_enum_arg", 0) >= 1
        out = _assert_routes_byte_identical(src)
        assert "w.tune(::std::memory_order::RELAXED)" in out[1]

    def test_template_tparam_arg_routes_byte_identical(self):
        src = (_MO
               + "@native('tpy::Slot')\n"
               + "class Slot[T]:\n"
               + "    def __init__(self) -> None: ...\n"
               + "    @cpp_template('{self}.put({0})')\n"
               + "    def put(self, v: T) -> None: ...\n"
               + "class Holder[T]:\n"
               + "    s: Slot[T]\n"
               + "    def __init__(self) -> None:\n"
               + "        self.s = Slot[T]()\n"
               + "    def put(self, v: T) -> None:\n"
               + "        self.s.put(v)\n"
               + "def main() -> None:\n"
               + "    h = Holder[Int32]()\n"
               + "    h.put(3)\n"
               + "main()\n")
        _, witnesses = _lower_ctx_witnessed(src)
        assert witnesses.get("method.ptr_template_tparam_arg", 0) >= 1
        _assert_routes_byte_identical(src)

    STR_ARG = (_MO
               + "@native('tpy::MovableAtomic')\n"
               + "class Raw[T]:\n"
               + "    def __init__(self, value: T) -> None: ...\n"
               + "    @native('tpy::atomic_tag', function=True)\n"
               + "    def tag(self, name: str) -> Int32: ...\n"
               + "class At[T]:\n"
               + "    _raw: Raw[T]\n"
               + "    def __init__(self, value: T) -> None:\n"
               + "        self._raw = Raw[T](value)\n"
               + "    def tag(self, name: str) -> Int32:\n"
               + "        return self._raw.tag(name)\n"
               + "def main() -> None:\n"
               + "    a = At[Int32](1)\n"
               + "    print(a.tag('x'))\n"
               + "main()\n")

    def test_str_arg_stays_ast(self):
        # A view-family arg respells through the view rows, which this
        # family's bare interpolation does not mirror.
        _assert_rejects_at(_fallback(self.STR_ARG), "body:expr.method_call",
                           shape="method.ptr_template.arg_shape")
        _assert_byte_identical(self.STR_ARG)

    CLOSED_TUPLE = (_MO
                    + "@native('tpy::MovableAtomic')\n"
                    + "class Raw[T]:\n"
                    + "    def __init__(self, value: T) -> None: ...\n"
                    + "    @native('tpy::atomic_pair', function=True)\n"
                    + "    def pair(self, s: MO) -> tuple[bool, Int32]: ...\n"
                    + "class At[T]:\n"
                    + "    _raw: Raw[T]\n"
                    + "    def __init__(self, value: T) -> None:\n"
                    + "        self._raw = Raw[T](value)\n"
                    + "    def pair(self, s: MO) -> tuple[bool, Int32]:\n"
                    + "        return self._raw.pair(s)\n"
                    + "def main() -> None:\n"
                    + "    a = At[Int32](1)\n"
                    + "    ok, n = a.pair(MO.RELAXED)\n"
                    + "    print(ok)\n"
                    + "main()\n")

    def test_closed_tuple_result_stays_ast(self):
        # Only the OPEN-T tuple has borrow and storage coinciding; a closed
        # tuple result keeps its own family's rows.
        _assert_rejects_at(_fallback(self.CLOSED_TUPLE),
                           "body:expr.method_call",
                           shape="method.ptr_template.ret_type")
        _assert_byte_identical(self.CLOSED_TUPLE)


_SLOT = ("from tpy import Int32, readonly\n"
         "class Inner:\n"
         "    v: Int32\n"
         "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
         "class Slot:\n"
         "    opt: Inner | None\n"
         "    def __init__(self) -> None:\n        self.opt = None\n")


class TestOptionalToPtrOverContainerSubscript:
    """The optional_to_ptr decl lift whose field source hangs off a
    container-element subscript. Corpus witness: asyncio/_executor's
    `self.slots[i].box`."""

    MUTABLE = (_SLOT
               + "class Holder:\n"
               + "    slots: list[Slot]\n"
               + "    def __init__(self) -> None:\n"
               + "        self.slots = [Slot()]\n"
               + "    def peek(self, i: Int32) -> Int32:\n"
               + "        box = self.slots[i].opt\n"
               + "        if box is None:\n            return 0\n"
               + "        return box.v\n"
               + "def main() -> None:\n"
               + "    h = Holder()\n"
               + "    print(h.peek(0))\n"
               + "main()\n")

    def test_list_receiver_routes_byte_identical(self):
        out = _assert_routes_byte_identical(self.MUTABLE)
        assert ("Inner* box = ::tpy::optional_to_ptr("
                "::tpy::__getitem__(this->slots, i).opt);") in "".join(out)

    def test_readonly_receiver_renders_const_and_routes(self):
        # The const flavour: a @readonly method's element borrow is deep-const,
        # so the lifted pointer must spell `const Inner*`.
        src = self.MUTABLE.replace("    def peek(self, i: Int32)",
                                   "    @readonly\n    def peek(self, i: Int32)")
        out = _assert_routes_byte_identical(src)
        assert ("const Inner* box = ::tpy::optional_to_ptr("
                "::tpy::__getitem__(this->slots, i).opt);") in "".join(out)

    def test_dict_receiver_routes_byte_identical(self):
        src = (_SLOT
               + "class Holder:\n"
               + "    slots: dict[Int32, Slot]\n"
               + "    def __init__(self) -> None:\n"
               + "        self.slots = {}\n"
               + "    def peek(self, i: Int32) -> Int32:\n"
               + "        box = self.slots[i].opt\n"
               + "        if box is None:\n            return 0\n"
               + "        return box.v\n"
               + "def main() -> None:\n"
               + "    h = Holder()\n"
               + "    print(h.peek(0))\n"
               + "main()\n")
        _assert_routes_byte_identical(src)

    def test_local_container_receiver_routes_byte_identical(self):
        src = (_SLOT
               + "def peek(slots: list[Slot], i: Int32) -> Int32:\n"
               + "    box = slots[i].opt\n"
               + "    if box is None:\n        return 0\n"
               + "    return box.v\n"
               + "def main() -> None:\n"
               + "    xs = [Slot()]\n"
               + "    print(peek(xs, 0))\n"
               + "main()\n")
        _assert_routes_byte_identical(src)

    REASSIGNED = (_SLOT
                  + "def peek(slots: list[Slot], i: Int32) -> Int32:\n"
                  + "    box = slots[i].opt\n"
                  + "    box = slots[0].opt\n"
                  + "    if box is None:\n        return 0\n"
                  + "    return box.v\n"
                  + "def main() -> None:\n"
                  + "    xs = [Slot()]\n"
                  + "    print(peek(xs, 0))\n"
                  + "main()\n")

    def test_reassigned_binding_stays_ast(self):
        # The reseatable sibling is a different binding kind with its own
        # unwitnessed reseat lift -- the single-assignment lift must not
        # claim it.
        _assert_rejects_at(_fallback(self.REASSIGNED), "body:stmt.var_decl",
                           shape="decl.opt_reseat_source")
        _assert_byte_identical(self.REASSIGNED)

    OPTIONAL_ELEM = (_SLOT
                     + "def peek(slots: list[Slot | None], i: Int32) -> Int32:\n"
                     + "    box = slots[i].opt\n"
                     + "    if box is None:\n        return 0\n"
                     + "    return box.v\n"
                     + "def main() -> None:\n"
                     + "    xs: list[Slot | None] = [Slot()]\n"
                     + "    print(peek(xs, 0))\n"
                     + "main()\n")

    def test_optional_element_receiver_stays_ast(self):
        # An unproven-None ELEMENT makes the receiver itself nullable, which
        # takes the runtime deref check rather than the plain borrow lvalue.
        _assert_rejects_at(_fallback(self.OPTIONAL_ELEM), "body:stmt.var_decl",
                           shape="decl.slot_type")
        _assert_byte_identical(self.OPTIONAL_ELEM)


class TestSameProtocolUnionForward:
    """Forwarding a source already typed as the SAME structural-protocol
    union into that union's slot. Corpus witness: tplib/array_list's
    `ArrayList.__init__` calling `self.extend(items)`."""

    _HEAD = ("from typing import Protocol, Iterable\n"
             "from tpy import Int32, Own, Spannable\n")
    _EXTEND = ("    def extend(self, items: Spannable[T] | Iterable[Own[T]]"
               ") -> None:\n        self.n += 1\n")

    NARROWED_OPT = (_HEAD
                    + "class Bag[T]:\n"
                    + "    n: Int32\n"
                    + "    def __init__(self, items: Spannable[T] | "
                    + "Iterable[Own[T]] | None = None) -> None:\n"
                    + "        self.n = 0\n"
                    + "        if items is not None:\n"
                    + "            self.extend(items)\n"
                    + _EXTEND
                    + "def main() -> None:\n"
                    + "    b = Bag[Int32]([1, 2, 3])\n"
                    + "    print(b.n)\n"
                    + "main()\n")

    def test_narrowed_optional_forward_routes_byte_identical(self):
        out = _assert_routes_byte_identical(self.NARROWED_OPT)
        # The load-bearing agreement: the AST's consuming `own_iter` rewrite
        # is keyed on a BARE `Iterable[Own[T]]` slot and misses a union, so
        # both paths forward the source unwrapped. This fails the day that
        # keying widens.
        joined = "".join(out)
        assert "this->extend((*items));" in joined
        assert "own_iter" not in joined

    PLAIN_UNION = (_HEAD
                   + "class Bag[T]:\n"
                   + "    n: Int32\n"
                   + "    def __init__(self) -> None:\n        self.n = 0\n"
                   + "    def seed(self, items: Spannable[T] | "
                   + "Iterable[Own[T]]) -> None:\n"
                   + "        self.extend(items)\n"
                   + _EXTEND)

    def test_plain_union_forward_routes_bare(self):
        src = (self.PLAIN_UNION
               + "def main() -> None:\n"
               + "    b = Bag[Int32]()\n"
               + "    print(b.n)\n"
               + "main()\n")
        _, witnesses = _lower_ctx_witnessed(src)
        assert witnesses.get("method.struct_proto_union_arg", 0) >= 1
        out = _assert_routes_byte_identical(src)
        assert "this->extend(items);" in "".join(out)

    DIFFERENT_UNION = (_HEAD
                       + "class Sized(Protocol):\n"
                       + "    def size(self) -> Int32: ...\n"
                       + "class Bag[T]:\n"
                       + "    n: Int32\n"
                       + "    def __init__(self) -> None:\n        self.n = 0\n"
                       + "    def seed(self, items: Spannable[T] | "
                       + "Iterable[Own[T]]) -> None:\n"
                       + "        self.take(items)\n"
                       + "    def take(self, x: Spannable[T] | "
                       + "Iterable[Own[T]] | Sized) -> None:\n"
                       + "        self.n += 1\n"
                       + "def main() -> None:\n"
                       + "    b = Bag[Int32]()\n"
                       + "    print(b.n)\n"
                       + "main()\n")

    def test_different_union_stays_ast(self):
        # A slot union that is not the source's own would have to re-select a
        # branch, so only exact identity forwards.
        _assert_rejects_at(_fallback(self.DIFFERENT_UNION),
                           "body:expr.method_call", shape="method.arg_shape")
        _assert_byte_identical(self.DIFFERENT_UNION)

    DYN_MEMBER = ("from typing import Protocol, Iterable\n"
                  + "from tpy import Int32, Own, Spannable, dynamic\n"
                  + "@dynamic\n"
                  + "class Tagged(Protocol):\n"
                  + "    def tag(self) -> Int32: ...\n"
                  + "class Bag[T]:\n"
                  + "    n: Int32\n"
                  + "    def __init__(self) -> None:\n        self.n = 0\n"
                  + "    def seed(self, items: Spannable[T] | Tagged) -> None:\n"
                  + "        self.extend(items)\n"
                  + "    def extend(self, items: Spannable[T] | Tagged) -> None:\n"
                  + "        self.n += 1\n"
                  + "def main() -> None:\n"
                  + "    b = Bag[Int32]()\n"
                  + "    print(b.n)\n"
                  + "main()\n")

    def test_dynamic_member_union_stays_ast(self):
        # A @dynamic member takes the adapter wrap, so the union is not a
        # pure concept selection any more.
        _assert_rejects_at(_fallback(self.DYN_MEMBER), "body:expr.method_call",
                           shape="method.arg_shape")
        _assert_byte_identical(self.DYN_MEMBER)


class TestOwnViewFamilyCtorFieldMove:
    """An `Own[str]` / `Own[bytes]` param moving into the owned view-family
    field's member init. Corpus witness: tplib/requests' `FileField`."""

    BYTES_MOVE = ("from tpy import Int32, Own\n"
                  "class F:\n"
                  "    name: str\n"
                  "    content: bytes\n"
                  "    def __init__(self, name: str,\n"
                  "                 content: Own[bytes]) -> None:\n"
                  "        self.name = name\n"
                  "        self.content = content\n")

    def test_bytes_move_routes_byte_identical(self):
        ctor = _lower_ctor(self.BYTES_MOVE, "F")
        assert ctor is not None
        assert _ctor_tail(ctor) == (
            " : name(name), content(std::move(content)) {}\n")
        _assert_byte_identical(
            self.BYTES_MOVE
            + "def main() -> None:\n"
            + "    f = F('a', bytes())\n"
            + "    print(len(f.content))\n"
            + "main()\n")

    STR_MOVE = ("from tpy import Int32, Own\n"
                "class F:\n"
                "    text: str\n"
                "    def __init__(self, text: Own[str]) -> None:\n"
                "        self.text = text\n")

    def test_str_move_routes_byte_identical(self):
        ctor = _lower_ctor(self.STR_MOVE, "F")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : text(std::move(text)) {}\n"
        _assert_byte_identical(
            self.STR_MOVE
            + "from tpy import String\n"
            + "def main() -> None:\n"
            + "    f = F(String('hi'))\n"
            + "    print(len(f.text))\n"
            + "main()\n")

    def test_bytes_non_last_use_stays_ast(self):
        # Not a move: the param is read again, so the AST copies into the
        # field instead -- a render this row does not spell.
        src = ("from tpy import Int32, Own\n"
               "class F:\n"
               "    n: Int32\n"
               "    content: bytes\n"
               "    def __init__(self, content: Own[bytes]) -> None:\n"
               "        self.content = content\n"
               "        self.n = Int32(len(content))\n")
        assert _lower_ctor(src, "F") is None
        _assert_byte_identical(
            src + "def main() -> None:\n"
            + "    print(F(bytes()).n)\n"
            + "main()\n")

    def test_str_non_last_use_stays_ast(self):
        src = ("from tpy import Int32, Own, String\n"
               "class F:\n"
               "    n: Int32\n"
               "    text: str\n"
               "    def __init__(self, text: Own[str]) -> None:\n"
               "        self.text = text\n"
               "        self.n = Int32(len(text))\n")
        assert _lower_ctor(src, "F") is None
        _assert_byte_identical(
            src + "def main() -> None:\n"
            + "    print(F(String('hi')).n)\n"
            + "main()\n")

    def test_plain_view_param_keeps_the_copy_row(self):
        # The non-Own sibling still takes the view arm's owned copy, so the
        # move admission must not have displaced it.
        src = ("from tpy import Int32\n"
               "class F:\n"
               "    content: bytes\n"
               "    def __init__(self, content: bytes) -> None:\n"
               "        self.content = content\n")
        ctor = _lower_ctor(src, "F")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : content(::tpy::bytes_copy(content)) {}\n"
        _assert_routes_byte_identical(
            src + "def main() -> None:\n"
            + "    f = F(bytes())\n"
            + "    print(len(f.content))\n"
            + "main()\n")


_WSLOT = ("from tpy import Int32\n"
          "class Inner:\n"
          "    v: Int32\n"
          "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
          "class Slot:\n"
          "    opt: Inner | None\n"
          "    n: Int32\n"
          "    def __init__(self) -> None:\n"
          "        self.opt = None\n        self.n = 0\n")

_WHOLDER = ("class Holder:\n"
            "    slots: list[Slot]\n"
            "    def __init__(self) -> None:\n"
            "        self.slots = [Slot()]\n")

_WMAIN = ("def main() -> None:\n"
          "    h = Holder()\n"
          "    print(h.clear(0))\n"
          "main()\n")


class TestOptionalNoneWriteOverSubscript:
    """`recv[i].opt = None` at an Optional FIELD. A SUBSCRIPT receiver -- a
    container element or a record-typed tuple element -- is a plain record
    borrow lvalue, so the member spells the same postfix off the bare element
    read that the scalar field write already renders there; the
    `std::nullopt` value render is receiver-blind, so only the target varies.

    Corpus witness: asyncio/_executor's `self.slots[i].box = None`. The write
    twin of the optional_to_ptr READ lift over the same receiver."""

    def test_container_element_receiver_routes(self):
        src = (_WSLOT + _WHOLDER
               + "    def clear(self, i: Int32) -> bool:\n"
               + "        self.slots[i].opt = None\n"
               + "        return True\n"
               + _WMAIN)
        out = _assert_routes_byte_identical(src)
        assert ("::tpy::__getitem__(this->slots, i).opt = std::nullopt;"
                in "".join(out))

    def test_dict_element_receiver_routes(self):
        src = (_WSLOT
               + "class Holder:\n"
               + "    slots: dict[Int32, Slot]\n"
               + "    def __init__(self) -> None:\n"
               + "        self.slots = {}\n"
               + "    def clear(self, i: Int32) -> bool:\n"
               + "        self.slots[i].opt = None\n"
               + "        return True\n"
               + _WMAIN)
        _assert_routes_byte_identical(src)

    def test_value_repr_optional_field_routes(self):
        # The inner's repr does not reach the render: storage is
        # `std::optional<T>` either way, so a value inner takes the same
        # `std::nullopt`.
        src = ("from tpy import Int32\n"
               "class Slot:\n"
               "    opt: Int32 | None\n"
               "    def __init__(self) -> None:\n        self.opt = None\n"
               + _WHOLDER
               + "    def clear(self, i: Int32) -> bool:\n"
               + "        self.slots[i].opt = None\n"
               + "        return True\n"
               + _WMAIN)
        _assert_routes_byte_identical(src)

    def test_tuple_element_receiver_routes(self):
        # The subscript family's other half: a record-typed TUPLE element,
        # storage flavour (`std::get<N>(t).opt`).
        src = (_WSLOT
               + "class Holder:\n"
               + "    pair: tuple[Slot, Int32]\n"
               + "    def __init__(self) -> None:\n"
               + "        self.pair = (Slot(), 1)\n"
               + "    def clear(self, i: Int32) -> bool:\n"
               + "        self.pair[0].opt = None\n"
               + "        return True\n"
               + _WMAIN)
        out = _assert_routes_byte_identical(src)
        assert "std::get<0>(this->pair).opt = std::nullopt;" in "".join(out)

    def test_tuple_local_receiver_routes(self):
        src = (_WSLOT
               + "def clear() -> bool:\n"
               + "    s = Slot()\n"
               + "    t = (s, 1)\n"
               + "    t[0].opt = None\n"
               + "    return True\n"
               + "def main() -> None:\n"
               + "    print(clear())\n"
               + "main()\n")
        out = _assert_routes_byte_identical(src)
        assert "std::get<0>(t).opt = std::nullopt;" in "".join(out)

    def test_non_none_value_at_the_same_receiver_stays_ast(self):
        # BOUNDARY: only the None row got the subscript receiver. A record
        # SOURCE carries a move/copy verdict and a storage lift the None
        # render has nothing to say about, and those families still gate on
        # a name receiver.
        src = (_WSLOT + _WHOLDER
               + "    def clear(self, i: Int32) -> bool:\n"
               + "        self.slots[i].opt = Inner(1)\n"
               + "        return True\n"
               + _WMAIN)
        _assert_rejects_at(_fallback(src), "body:stmt.assign",
                           "assign.field_write_shape")
        _assert_byte_identical(src)

    def test_field_chained_over_the_subscript_stays_ast(self):
        # BOUNDARY: `recv[i].mid.opt` is a FIELD receiver whose own receiver
        # is the element -- a chain, not the subscript shape this row names.
        src = ("from tpy import Int32\n"
               "class Inner:\n"
               "    v: Int32\n"
               "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
               "class Mid:\n"
               "    opt: Inner | None\n"
               "    def __init__(self) -> None:\n        self.opt = None\n"
               "class Slot:\n"
               "    mid: Mid\n"
               "    def __init__(self) -> None:\n        self.mid = Mid()\n"
               + _WHOLDER
               + "    def clear(self, i: Int32) -> bool:\n"
               + "        self.slots[i].mid.opt = None\n"
               + "        return True\n"
               + _WMAIN)
        _assert_rejects_at(_fallback(src), "body:stmt.assign",
                           "assign.field_write_shape")
        _assert_byte_identical(src)
