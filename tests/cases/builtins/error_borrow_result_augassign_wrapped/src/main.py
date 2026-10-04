# A borrow-declared call ANYWHERE in an augmented-assignment target runs twice
# in the render, not only as the direct receiver: wrapped in a call
# (`wrap(next(it, d)).v += 10`, pinned here), behind a method hop
# (`next(it, d).get_q().v += 10`) or in an index (`xs[next(it, d).v] += 5`)
# -- all refused alike until aug-assign binds its target once
# (BUGS.md#augassign-call-receiver-double-eval).
class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def wrap(p: P) -> P:
    return p


def main() -> None:
    ps = [P(1), P(2)]
    it = iter(ps)
    d = P(0)
    wrap(next(it, d)).v += 10  # tpyc: error(/'next\(\.\.\.\)' is evaluated twice by an augmented assignment/)
    print(ps[0].v, ps[1].v)


main()
