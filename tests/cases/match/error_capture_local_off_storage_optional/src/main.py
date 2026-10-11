# A loop variable captured into an existing pointer local rejects:
# BUGS.md#match-capture-loop-var-write-not-credited, BUGS.md#match-capture-existing-local-not-rebound.
class P:
    def __init__(self, n: int) -> None:
        self.n = n


def last_or(xs: list[P | None], p: P | None) -> int:
    v = p
    for x in xs:
        match x:  # tpyc: error(/match\.capture_const_loop_var/)
            case v:
                pass
    if v is not None:
        v.n += 100
    t = 0
    if p is not None:
        t += p.n
    return t


def main() -> None:
    xs: list[P | None] = [P(1)]
    print(last_or(xs, P(2)))


main()
