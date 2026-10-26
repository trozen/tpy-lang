# `raise X(args)` copies an argument, warned, where the expression `X(args)`
# does; a later write to the source does not reach the raised object.
from typing import Iterator, Protocol, Sized
from tpy import Own, dynamic, int32, readonly


class P:
    def __init__(self, v: int32) -> None:
        self.v = v


class OwnErr(Exception):
    def __init__(self, p: Own[P]) -> None:
        super().__init__("own")
        self.p = p


@dynamic
class Sizes(Protocol):
    @readonly
    def size(self) -> int32: ...


class Carrier(Exception, Sizes):
    """The non-generic base a generic exception is caught by
    (BUGS.md#generic-exception-except-clause); `size` reads the payload."""

    @readonly
    def size(self) -> int32:
        return -1


class GenOwn[T: Sized](Carrier):
    def __init__(self, x: Own[T]) -> None:
        super().__init__("gen")
        self.x = x

    @readonly
    def size(self) -> int32:
        return len(self.x)


class Bump(Exception):
    v: int32

    def __init__(self, p: P) -> None:
        super().__init__("bump")
        p.v += 1
        self.v = p.v


def key_of(p: P) -> int32:
    return p.v


def walk(ps: list[P]) -> Iterator[P]:
    for p in ps:
        yield p


def own_twin(ps: list[P]) -> None:
    p = ps[0]
    e = OwnErr(p)  # tpyc: warning(/copies P into owned storage/)
    raise e


def own_stmt(ps: list[P]) -> None:
    p = ps[0]
    # borrowed record into an owning constructor parameter: copied
    raise OwnErr(p)  # tpyc: warning(/copies P into owned storage/)


def bump_twin(ps: list[P]) -> None:
    e = Bump(max(walk(ps), key=key_of))  # tpyc: warning(/copies P into argument 'p' of 'Bump/)
    raise e


def bump_stmt(ps: list[P]) -> None:
    # a generator's borrowed element into a parameter the constructor writes
    raise Bump(max(walk(ps), key=key_of))  # tpyc: warning(/copies P into argument 'p' of 'Bump/)


def gen_stmt(ys: list[int32]) -> None:
    # a generic class's Own slot, its type argument inferred: copied
    raise GenOwn(ys)  # tpyc: warning(/copies list\[int32\]/)


def gen_written_stmt() -> None:
    zs = [1]
    try:
        # the type argument written, from a local used after the raise: copied
        raise GenOwn[list[int32]](zs)  # tpyc: warning(/copies list\[int32\]/)
    except Carrier as e:
        zs.append(2)
        print("gen-written-stmt", e.size(), len(zs))


def main() -> None:
    ps = [P(1)]
    try:
        own_twin(ps)
    except OwnErr as e:
        ps[0].v = 10
        print("own-twin", e.p.v, ps[0].v)
    qs = [P(1)]
    try:
        own_stmt(qs)
    except OwnErr as e:
        qs[0].v = 10
        print("own-stmt", e.p.v, qs[0].v)
    rs = [P(1), P(3)]
    try:
        bump_twin(rs)
    except Bump as e:
        print("bump-twin", e.v, rs[1].v)
    ss = [P(1), P(3)]
    try:
        bump_stmt(ss)
    except Bump as e:
        print("bump-stmt", e.v, ss[1].v)
    ys = [1]
    try:
        gen_stmt(ys)
    except Carrier as e:
        ys.append(2)
        print("gen-stmt", e.size(), len(ys))
    gen_written_stmt()


main()
