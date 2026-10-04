# Rebinding an existing local to a call that hands back a reference which
# may point into a temporary operand of a borrow-declared call
# (`m = k.ret(min(a, P(-i - 1), key=f))` in a loop) is refused: the operand
# would be hoisted into a local of the loop body, which dies before `m` is
# read after the loop. A DECLARATION in the same block hoists and binds
# (builtins/borrow_result); the bare `m = min(a, P(-8), key=f)` rebind is
# refused the same way (BUGS.md#lend-back-of-hoisted-temp-warned-as-dangling).
class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def key_of(p: P) -> int:
    return p.v


class K:
    def ret(self, p: P) -> P:
        p.v += 1
        return p


def main() -> None:
    a = P(5)
    k = K()
    m = a
    for i in range(2):
        m = k.ret(min(a, P(-i - 1), key=key_of))  # tpyc: error(/not yet supported/)
    print(m.v)


main()
