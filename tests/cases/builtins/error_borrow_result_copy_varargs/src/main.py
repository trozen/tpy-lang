# A COPY result (max over an iterator) as a `*args` element is judged at the
# element type: copied into a written `*ps: P` element it warns, into a
# `*ps: readonly[P]` one it does not (diag.txt pins both). The call itself
# is the filed lowering gap BUGS.md#copy-result-at-varargs-slot-rejects.
from typing import Iterator
from tpy import readonly


class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def key_of(p: P) -> int:
    return p.v


def walk(ps: list[P]) -> Iterator[P]:
    for p in ps:
        yield p


def read_all(*ps: readonly[P]) -> int:
    t = 0
    for p in ps:
        t += p.v
    return t


def bump_all(*ps: P) -> None:
    for p in ps:
        p.v += 1000


def main() -> None:
    ps = [P(3), P(9)]
    # a readonly element: no copy warning
    print(read_all(max(walk(ps), key=key_of)))  # tpyc: error(/not yet supported by C\+\+ code generation/)
    # a written element: the copy warns
    bump_all(max(walk(ps), key=key_of))  # tpyc: warning(/copies P into argument 'ps' of 'bump_all/)
    print(ps[0].v, ps[1].v)


main()
