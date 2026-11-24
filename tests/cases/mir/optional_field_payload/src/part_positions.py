# The payload holder and the payload field write at each position; every section writes
# through the holder and reads the change back through the field.
from typing import Iterator
from tpy import int32
from fixtures import H, P


class Node:
    o: P | None

    # constructor: the payload held once the field is set
    def __init__(self, v: int32) -> None:  # tpyc: mir(covered)
        self.o = P(v)
        if self.o is not None:
            r = self.o  # tpyc: ok
            r.v += 1

    # method: a holder of self's payload
    def bump(self) -> int32:  # tpyc: mir(covered) mir_summary(known)
        if self.o is not None:
            r = self.o  # tpyc: mir_borrows(r, self.o[payload])
            r.v += 1
            return self.o.v
        return -1

    # method: the payload's field written through self
    def put(self, v: int32) -> None:  # tpyc: mir(covered) mir_summary(known)
        if self.o is not None:
            self.o.v = v


# loop body
def loop_body(h: H) -> int32:  # tpyc: mir(covered)
    total = 0
    for i in range(3):
        if h.o is not None:
            r = h.o
            r.v += i
            total += h.o.v
    return total


# while arm
def while_arm(h: H) -> int32:  # tpyc: mir(covered)
    n = 0
    while h.o is not None and n < 2:
        h.o.v += 1
        n += 1
    return h.o.v if h.o is not None else -1


# match arm: MIR does not lower a match statement yet (a coverage gap)
def match_arm(h: H, k: int32) -> int32:  # tpyc: mir(uncovered /unsupported statement/)
    match k:
        case 1:
            if h.o is not None:
                r = h.o
                r.v = 40
                return h.o.v
        case _:
            pass
    return 0


# an element bound first, then its payload held: a list of records is not lowered yet
def element(hs: list[H]) -> int32:  # tpyc: mir(uncovered /unsupported native container element/)
    h = hs[0]
    if h.o is not None:
        r = h.o
        r.v = 50
        g = hs[0]
        return g.o.v if g.o is not None else -1
    return 0


# a generator keeps its own refusal
def gen_payload(h: H) -> Iterator[int32]:  # tpyc: mir(uncovered /unsupported hoisted value/)
    if h.o is not None:
        r = h.o
        yield r.v


def main() -> None:
    n = Node(1)
    print("positions ctor", n.o.v if n.o is not None else -1)
    print("positions method", n.bump())
    n.put(9)
    print("positions put", n.o.v if n.o is not None else -1)
    print("positions loop", loop_body(H(1)))
    print("positions while", while_arm(H(1)))
    print("positions match", match_arm(H(1), 1), match_arm(H(1), 2))
    print("positions element", element([H(1)]))
    for x in gen_payload(H(3)):
        print("positions gen", x)
