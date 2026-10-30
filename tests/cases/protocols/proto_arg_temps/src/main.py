# A protocol method argument that needs a hoisted temporary compiles like its
# record-method twin, for every protocol receiver form (one section each).
# The Own[...] sections pass a LIVE source, so TPy copies it (the warned
# implicit copy, as at a record receiver): neither side mutates across them.
from typing import Optional, Protocol
from tpy import Own, dynamic, int32, readonly


class R:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Other:
    y: int32

    def __init__(self, y: int32) -> None:
        self.y = y


class B:
    v: str

    def __init__(self) -> None:
        self.v = "field"


class Sink(Protocol):
    def take_rec(self, p: Own[R]) -> int32: ...
    def take_list(self, p: Own[list[int32]]) -> int32: ...
    def take_str(self, p: Own[str]) -> int32: ...
    def take_union(self, u: int32 | str) -> int32: ...
    def take_ru(self, p: readonly[R | Other]) -> int32: ...


@dynamic
class DSink(Protocol):
    def take_rec(self, p: Own[R]) -> int32: ...
    def take_list(self, p: Own[list[int32]]) -> int32: ...
    def take_str(self, p: Own[str]) -> int32: ...
    def take_union(self, u: int32 | str) -> int32: ...
    def take_ru(self, p: readonly[R | Other]) -> int32: ...


class Impl(DSink):
    recs: list[R]
    lists: list[list[int32]]

    def __init__(self) -> None:
        self.recs = []
        self.lists = []

    def take_rec(self, p: Own[R]) -> int32:
        v = p.x + 100
        self.recs.append(p)
        return v

    def take_list(self, p: Own[list[int32]]) -> int32:
        n = len(p) + 10
        self.lists.append(p)
        return n

    def take_str(self, p: Own[str]) -> int32:
        return len(p)

    def take_union(self, u: int32 | str) -> int32:
        return len(str(u))

    def take_ru(self, p: readonly[R | Other]) -> int32:
        if isinstance(p, R):
            return p.x
        return p.y


def via_proto(tag: str, sk: Sink, pv: list[int32],
              opt: Optional[list[int32]]) -> None:
    # Structural protocol receiver.
    r0 = R(1)
    print(tag, "rec_own local", sk.take_rec(r0), r0.x)  # tpyc: warning(/copies R into owned storage/)
    for r1 in [R(2), R(3)]:
        print(tag, "rec_own loop", sk.take_rec(r1), r1.x)  # tpyc: warning(/copies R into owned storage/)
    print(tag, "list_own param", sk.take_list(pv), len(pv))  # tpyc: warning(/copies list\[int32\] into owned storage/)
    if opt is not None:
        print(tag, "list_own narrowed", sk.take_list(opt), len(opt))  # tpyc: warning(/copies list\[int32\] into owned storage/)
    b = B()
    print(tag, "str_own field", sk.take_str(b.v), b.v)  # tpyc: ok
    s = str(len(pv)) + "x"
    print(tag, "str_own owned", sk.take_str(s), s)  # tpyc: ok
    n = 7
    xs = [1, 22]
    print(tag, "union local", sk.take_union(n))  # tpyc: ok
    print(tag, "union subscript", sk.take_union(xs[1]))  # tpyc: ok
    print(tag, "union ctor", sk.take_ru(R(6)))  # tpyc: ok


def via_dyn(tag: str, sk: DSink, pv: list[int32],
            opt: Optional[list[int32]]) -> None:
    # @dynamic protocol receiver.
    r0 = R(1)
    print(tag, "rec_own local", sk.take_rec(r0), r0.x)  # tpyc: warning(/copies R into owned storage/)
    for r1 in [R(2), R(3)]:
        print(tag, "rec_own loop", sk.take_rec(r1), r1.x)  # tpyc: warning(/copies R into owned storage/)
    print(tag, "list_own param", sk.take_list(pv), len(pv))  # tpyc: warning(/copies list\[int32\] into owned storage/)
    if opt is not None:
        print(tag, "list_own narrowed", sk.take_list(opt), len(opt))  # tpyc: warning(/copies list\[int32\] into owned storage/)
    b = B()
    print(tag, "str_own field", sk.take_str(b.v), b.v)  # tpyc: ok
    s = str(len(pv)) + "x"
    print(tag, "str_own owned", sk.take_str(s), s)  # tpyc: ok
    n = 7
    xs = [1, 22]
    print(tag, "union local", sk.take_union(n))  # tpyc: ok
    print(tag, "union subscript", sk.take_union(xs[1]))  # tpyc: ok
    print(tag, "union ctor", sk.take_ru(R(6)))  # tpyc: ok


def via_bound[T: Sink](tag: str, sk: T, pv: list[int32],
                       opt: Optional[list[int32]]) -> None:
    # Type parameter bounded by a protocol.
    r0 = R(1)
    print(tag, "rec_own local", sk.take_rec(r0), r0.x)  # tpyc: warning(/copies R into owned storage/)
    for r1 in [R(2), R(3)]:
        print(tag, "rec_own loop", sk.take_rec(r1), r1.x)  # tpyc: warning(/copies R into owned storage/)
    print(tag, "list_own param", sk.take_list(pv), len(pv))  # tpyc: warning(/copies list\[int32\] into owned storage/)
    if opt is not None:
        print(tag, "list_own narrowed", sk.take_list(opt), len(opt))  # tpyc: warning(/copies list\[int32\] into owned storage/)
    b = B()
    print(tag, "str_own field", sk.take_str(b.v), b.v)  # tpyc: ok
    s = str(len(pv)) + "x"
    print(tag, "str_own owned", sk.take_str(s), s)  # tpyc: ok
    n = 7
    xs = [1, 22]
    print(tag, "union local", sk.take_union(n))  # tpyc: ok
    print(tag, "union subscript", sk.take_union(xs[1]))  # tpyc: ok
    print(tag, "union ctor", sk.take_ru(R(6)))  # tpyc: ok


def via_own(tag: str, sk: Own[DSink], pv: list[int32],
            opt: Optional[list[int32]]) -> Own[DSink]:
    # Owned @dynamic protocol receiver.
    r0 = R(1)
    print(tag, "rec_own local", sk.take_rec(r0), r0.x)  # tpyc: warning(/copies R into owned storage/)
    for r1 in [R(2), R(3)]:
        print(tag, "rec_own loop", sk.take_rec(r1), r1.x)  # tpyc: warning(/copies R into owned storage/)
    print(tag, "list_own param", sk.take_list(pv), len(pv))  # tpyc: warning(/copies list\[int32\] into owned storage/)
    if opt is not None:
        print(tag, "list_own narrowed", sk.take_list(opt), len(opt))  # tpyc: warning(/copies list\[int32\] into owned storage/)
    b = B()
    print(tag, "str_own field", sk.take_str(b.v), b.v)  # tpyc: ok
    s = str(len(pv)) + "x"
    print(tag, "str_own owned", sk.take_str(s), s)  # tpyc: ok
    n = 7
    xs = [1, 22]
    print(tag, "union local", sk.take_union(n))  # tpyc: ok
    print(tag, "union subscript", sk.take_union(xs[1]))  # tpyc: ok
    print(tag, "union ctor", sk.take_ru(R(6)))  # tpyc: ok
    return sk


class Opt(Protocol):
    def take_ol(self, p: Optional[list[int32]]) -> int32: ...
    def take_oi(self, p: Optional[int32]) -> int32: ...


class OptImpl:
    def __init__(self) -> None:
        pass

    def take_ol(self, p: Optional[list[int32]]) -> int32:
        if p is None:
            return -1
        p.append(9)
        return len(p)

    def take_oi(self, p: Optional[int32]) -> int32:
        if p is None:
            return -1
        return p


def via_opt(sk: Opt) -> None:
    # A list literal at a pointer-repr Optional slot hoists a typed
    # temporary passed by address; its value-repr scalar sibling binds the
    # rvalue in place.
    n = 4
    print("optptr list literal", sk.take_ol([1, 2]))  # tpyc: ok
    print("opt scalar", sk.take_oi(n + 1))  # tpyc: ok


class GSink[T](Protocol):
    def put(self, v: Own[T]) -> None: ...


class Bag[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def put(self, v: Own[T]) -> None:
        self.items.append(v)


def via_generic(sink: GSink[str], xs: list[str]) -> None:
    # Generic protocol at T = str: a live owned str copies into the Own[T] slot.
    s = xs[0] + "x"
    sink.put(s)  # tpyc: ok
    print("generic own_str", s)


@dynamic
class Bumper(Protocol):
    def bump(self) -> int32: ...


class Counter(Bumper):
    n: int32

    def __init__(self) -> None:
        self.n = 1

    def bump(self) -> int32:
        self.n += 1
        return self.n


@dynamic
class Taker(Protocol):
    def take_p(self, p: Bumper) -> int32: ...


class TakerImpl(Taker):
    def __init__(self) -> None:
        pass

    def take_p(self, p: Bumper) -> int32:
        return p.bump()


def via_dyn_param(sk: Taker) -> None:
    # A conformer rvalue at a @dynamic-protocol slot of a @dynamic receiver.
    print("dyn param ctor", sk.take_p(Counter()))  # tpyc: ok


def via_opt_dyn(o: Optional[DSink], xs: list[str]) -> None:
    # A None-narrowed Optional[@dynamic P] receiver.
    if o is None:
        return
    n = 7
    s = xs[0] + "x"
    print("optdyn union local", o.take_union(n))  # tpyc: ok
    print("optdyn str_own owned", o.take_str(s), s)  # tpyc: ok


class Pinger(Protocol):
    def ping(self) -> int32: ...


class Elem:
    n: int32

    def __init__(self) -> None:
        self.n = 3

    def ping(self) -> int32:
        self.n += 1
        return self.n


class ElemReader:
    def __init__(self) -> None:
        pass

    def take(self, p: Pinger) -> int32:
        return p.ping()


def take_free(p: Pinger) -> int32:
    return p.ping()


def via_elem(r: ElemReader, d: dict[str, Elem]) -> None:
    # A record element at a structural-protocol parameter binds the
    # caller's element: the write through the parameter reaches d["k"].
    # The protocol-receiver form of this call does not build yet
    # (BUGS.md#protocol-param-structural-protocol-concept), so the record
    # and free forms pin the render.
    print("elem record", r.take(d["k"]), d["k"].n)  # tpyc: ok
    print("elem free", take_free(d["k"]), d["k"].n)  # tpyc: ok


def main() -> None:
    via_proto("proto", Impl(), [1, 2], [3])
    via_dyn("dyn", Impl(), [1, 2], [3])
    via_bound("bound", Impl(), [1, 2], [3])
    via_own("own", Impl(), [1, 2], [3])
    via_opt(OptImpl())
    b = Bag[str]()
    via_generic(b, ["a"])
    print("generic items", b.items)
    via_dyn_param(TakerImpl())
    via_opt_dyn(Impl(), ["b"])
    via_elem(ElemReader(), {"k": Elem()})


main()
