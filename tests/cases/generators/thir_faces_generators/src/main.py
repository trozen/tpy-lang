# Generator-frame shapes no other case reaches: a walrus over a record local
# at the yield, and branch-local writes inside a generator body (a `global`
# assigned under an `if`, a protocol-typed local forwarding a parameter).
from typing import Iterable, Iterator
from tpy import int32


class Node:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def gen_walrus() -> Iterator[Node]:
    i = 0
    while i < 2:
        buf = Node(i)
        # The walrus aliases `buf`: a write through `buf` after the resume is
        # what `x` reads.
        yield (x := buf)  # tpyc: ok
        buf.v += 10
        print("yield_record_walrus: resume", buf.v, x.v)
        i += 1


def yield_record_walrus() -> None:
    for n in gen_walrus():
        n.v += 100
        print("yield_record_walrus:", n.v)


G = 0


def gen_global(flag: bool) -> Iterator[int32]:
    global G
    i = 0
    while i < 2:
        if flag:
            # A module global written inside a branch of the frame body.
            G = i + 10  # tpyc: ok
        yield G
        i += 1


def gen_fwd(it: Iterable[int32], flag: bool) -> Iterator[int32]:
    yield 0
    # The branch holds no suspension, so its statements lower as block locals.
    if flag:
        # A branch-local protocol-typed local forwarding the parameter.
        xs = it  # tpyc: ok
        for x in xs:
            print("branch_block_local: fwd", x)
    yield 1


def branch_block_local() -> None:
    for n in gen_global(True):
        print("branch_block_local: global", n)
    # The caller keeps the backing list and writes it between resumes: the
    # generator sees the store through `xs`, an alias of it, not a copy.
    backing = [1, 2]
    g = gen_fwd(backing, True)
    print("branch_block_local: yield", next(g, -1))
    backing[0] = 7
    for n in g:
        print("branch_block_local: yield", n)
    print("branch_block_local: G", G)


def main() -> None:
    yield_record_walrus()
    branch_block_local()


main()
