# A call's result may point into an argument unless the argument cannot
# hold what it points at: a temporary that may is warned, one per shape.
from tpy import int32, Own, Ptr, StrView, deref
from tpy.extern import native, cpp_template
# Not here: a generic class with a field of a same-module subclass fails
# the C++ build (BUGS.md#generic-record-subclass-field-emitted-first).
from fixtures import Base, Child, Node


class Holder[T]:
    def __init__(self, c: Own[Child], x: Own[T]) -> None:
        self.c = c
        self.x = x


class Key:
    def __init__(self, k: int32) -> None:
        self.k = k


@native
class SubPicker:
    def __init__(self) -> None: ...

    # Undecorated, it would lend from no operand
    # (BUGS.md#native-class-plain-method-lends-no-operand).
    @native("pick1")
    def pick1(self, x: Child) -> Base: ...


@native
class Opaque:
    def __init__(self) -> None: ...


@cpp_template("{0}.data()")
def data(x: Opaque) -> Ptr[int32]: ...


@native
class Finder:
    def __init__(self) -> None: ...

    @native("find")
    def find(self, k: Key) -> Node: ...

    @native("find", args={"k": "compared"})
    def find_cmp(self, k: Key) -> Node: ...


def pair[T](n: T) -> tuple[T, int32]:
    return (n, 1)


def base_of[T](h: Holder[T]) -> Base:
    return h.c  # tpyc: warning(/upcast narrows 'Child' to 'Base'/)


def opt_first[T](a: T, b: T) -> T | None:
    return a


@native
def opt_pair(b: Base) -> tuple[Base, int32] | None: ...


@native
def pair_with(b: Base, s: str) -> tuple[Base, int32]: ...


def keep_pair(b: Base) -> tuple[Base, int32]:
    s = "k" * 3
    # a `str` cannot hold the tuple's `Base`: returned over a local, as
    # a `-> Base` result would be
    return pair_with(b, s)  # tpyc: ok


class Text:
    def __init__(self, s: str) -> None:
        self.s = s

    def view(self) -> StrView:
        return self.s[1:]


class Note(Text):
    def via_self(self) -> int32:
        v: StrView = self.view()
        self.s += "!"  # tpyc: warning(/Mutation of 'self' while borrowed/)
        return len(v)

    def via_super(self) -> int32:
        # base-part calls lend `self` as `self.view()` does
        v: StrView = super().view()  # tpyc: ok
        self.s += "!"  # tpyc: warning(/Mutation of 'self' while borrowed/)
        return len(v)

    def via_base(self) -> int32:
        v: StrView = Text.view(self)  # tpyc: ok
        self.s += "!"  # tpyc: warning(/Mutation of 'self' while borrowed/)
        return len(v)


def main() -> None:
    # generic tuple result: its first element may be the argument
    t = pair(Base(1))  # tpyc: warning(/Result borrows from temporary argument 'n'/)
    # a subclass object held in a field of the argument
    b = base_of(Holder(Child(2), 1))  # tpyc: warning(/Result borrows from temporary argument 'h'/)
    p = SubPicker()
    # native method: a subclass argument may be the base result
    c = p.pick1(Child(3))  # tpyc: warning(/Result borrows from temporary argument 'x'/)
    ch = Child(3)
    p2 = SubPicker()
    d = p2.pick1(ch)  # tpyc: ok
    d.v = 4
    print("reach subclass:", ch.v)
    # generic Optional result at `bytearray`
    o = opt_first(bytearray(b"a"), bytearray(b"b"))  # tpyc: warning(/Result borrows from temporary argument 'a'/)
    # a pointer into an opaque native object (a `Ptr` result over a
    # temporary is not warned: BUGS.md#binding-ptr-result-over-temporary-unwarned;
    # returning it over a local is refused: error_opaque_ptr_result_over_local)
    ob = Opaque()
    q = data(ob)  # tpyc: ok
    print("reach opaque:", deref(q))
    f = Finder()
    # an unrelated class argument may hold the result: warned
    n = f.find(Key(1))  # tpyc: warning(/Result borrows from temporary argument 'k'/)
    # declared compared: the key holds nothing the result points at
    g = Finder()
    m = g.find_cmp(Key(1))  # tpyc: ok
    m.v += 1
    print("reach lookups:", m.v)
    # an Optional holding a tuple whose `Base` may be the argument
    e = opt_pair(Base(5))  # tpyc: warning(/Result borrows from temporary argument 'b'/)
    bb = Base(6)
    pe = opt_pair(bb)
    if pe is not None:
        pe[0].v = 7
    print("reach optional tuple:", bb.v)
    bk = Base(8)
    k = keep_pair(bk)
    k[0].v = 9
    print("reach tuple beside number:", bk.v, k[1])
    nt = Note("abc")
    print("reach base-part call:", nt.via_self(), nt.via_super(), nt.via_base())
