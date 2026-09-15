# An isinstance-condition ternary over a union with MORE than two members:
# `1 if isinstance(x, A) else 2`. The then arm still reads the subject through
# the condition-scoped inline get; the else arm gets no fact, because the
# complement (`B | C`) has no inline render -- and it needs none, since sema
# types a subject read there as the complement union and rejects it itself.
from tpy import int32


class A:
    def __init__(self, n: int32) -> None:
        self.n = n


class B:
    def __init__(self, m: int32) -> None:
        self.m = m


class C:
    def __init__(self, k: int32) -> None:
        self.k = k


# free function: neither arm reads the subject.
def pick(x: A | B | C) -> int32:
    return 1 if isinstance(x, A) else 2  # tpyc: ok


# free function: the then arm reads the checked member inline.
def then_reads(x: A | B | C) -> int32:
    return x.n if isinstance(x, A) else -1  # tpyc: ok


# free function: a str result over the same wide union.
def label(x: A | B | C) -> str:
    return "a" if isinstance(x, A) else "other"  # tpyc: ok


# free function: the TWO-member control, whose else arm keeps its complement
# fact and reads the subject.
def pair(x: A | B) -> int32:
    return x.n if isinstance(x, A) else x.m  # tpyc: ok


class Reader:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    # method: the same wide-union ternary inside a record method body.
    def pick(self, x: A | B | C) -> int32:
        return x.n if isinstance(x, A) else 0  # tpyc: ok


def main() -> None:
    a: A | B | C = A(7)
    b: A | B | C = B(8)
    c: A | B | C = C(9)
    print("pick", pick(a), pick(b), pick(c))
    print("then", then_reads(a), then_reads(c))
    print("label", label(a), label(b))
    p: A | B = A(3)
    q: A | B = B(4)
    print("pair", pair(p), pair(q))
    print("method", Reader("r").pick(a), Reader("r").pick(b))


main()
