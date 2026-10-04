# Two advances of one iterator in one expression: `next(it, d)` hands back a
# reference to the step, valid only until the next advance, so the first
# step would read the second (TPy `202 202`, CPython `101 202`). Refused as
# a stopgap until MIR models the step loan
# (BUGS.md#next-step-reference-outlived-by-advance); binding the first step
# to a name (a warned copy) is the spelling that works. A value-type element
# (`next(it, 0) + next(it, 0)`) copies each step and is not refused.
from typing import Iterator


class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def fresh(vals: list[int]) -> Iterator[P]:
    for v in vals:
        p = P(v)
        yield p


def main() -> None:
    g = fresh([101, 202, 303])
    d = P(0)
    t = (next(g, d).v, next(g, d).v)  # tpyc: error(/advances 'g' twice in one expression; bind the first step to a name/)
    print(t[0], t[1])


main()
