# Call arguments: a member value at a union parameter is converted at the
# member's own slot, then the union is built from it -- a pointer variant
# points at the object as its member, a value union is built explicitly.
from typing import Iterator
from tpy import int32, int64, uint32


class Base:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Sub(Base):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


def bump(x: Base | int32) -> int32:
    if isinstance(x, Base):
        x.n += 1
        return x.n
    return x


def peek(x: Base | int32) -> int32:
    if isinstance(x, Base):
        return x.n
    return x


def heads(x: Base | int32) -> Iterator[int32]:
    yield peek(x)


# the scalar twins of the union parameters above
def bump_twin(x: Base) -> int32:
    x.n += 1
    return x.n


def peek_twin(x: Base) -> int32:
    return x.n


def heads_twin(x: Base) -> Iterator[int32]:
    yield peek_twin(x)


class CM:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __enter__(self) -> "CM":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def gs(x: int | str) -> str:
    if isinstance(x, str):
        return x
    return "int"


def vo(x: int32 | str | None) -> int32:
    if isinstance(x, str):
        return len(x)
    if x is None:
        return -1
    return x


def widen(x: int64 | str) -> bool:
    return isinstance(x, str)


def gs_twin(x: str) -> str:
    return x


def vo_twin(x: int32) -> int32:
    return x


def widen_twin(x: int64) -> int64:
    return x


def fl(x: float | str) -> str:
    if isinstance(x, float):
        return "float " + str(x)
    return x


def bl(x: bool | str) -> str:
    if isinstance(x, bool):
        return "bool " + str(x)
    return x


def main() -> None:
    # pointer variant: a fresh subclass object is lent as its base member
    print("ptr-fresh", peek(Sub(5)))  # tpyc: ok
    # ... inline inside a conditional operand, never hoisted out of it
    for i in range(2):
        print("ptr-select", peek(Sub(i)) if i > 0 else 99)  # tpyc: ok
    # ... held for a for head and for a generator's frame
    for c in [peek(Sub(6))]:
        print("ptr-head", c)
    for c in heads(Sub(7)):
        print("ptr-frame", c)
    # ... and for a `with` head
    with CM(peek(Sub(8))) as cm:
        print("ptr-with", cm.v)
    print("ptr-twin", peek_twin(Sub(5)), peek_twin(Sub(1)))
    for c in [peek_twin(Sub(6))]:
        print("ptr-twin-head", c)
    for c in heads_twin(Sub(7)):
        print("ptr-twin-frame", c)
    # the callee writes the caller's object itself
    s = Sub(10)
    print("ptr-alias", bump(s), s.n)  # tpyc: ok
    s2 = Sub(10)
    print("ptr-alias-twin", bump_twin(s2), s2.n)
    # value union: an owned str is copied into the union built for the call
    t = "x" + str(3)
    print("value-str", gs(t), gs(t))  # tpyc: ok
    print("value-str-twin", gs_twin(t))
    # value union: a literal, a fresh str and a widened width
    print("value-lit", vo(3), vo("abc"), vo(None))  # tpyc: ok
    print("value-fresh", vo("x" + str(4)))  # tpyc: ok
    print("value-lit-twin", vo_twin(3))
    n: uint32 = 7
    print("value-widen", widen(n))  # tpyc: ok
    print("value-widen-twin", widen_twin(n))
    # value union: a float and a bool literal take their own members
    print("value-lit-kinds", fl(1.5), bl(True))  # tpyc: ok
