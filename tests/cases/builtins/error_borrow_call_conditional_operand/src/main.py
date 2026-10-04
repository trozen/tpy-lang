# A conditional operand of min / max proves nothing about the storage the
# result lends (either arm may be a temporary), and the argument render has
# no form for it yet: refused, as is an `or` operand
# (BUGS.md#borrow-call-walrus-conditional-operand-rejects).
class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def key_of(p: P) -> int:
    return p.v


def main() -> None:
    a = P(3)
    b = P(5)
    c = len(str(a.v)) > 5
    m = min(a if c else P(0), b, key=key_of)  # tpyc: error(/not yet supported/)
    print(m.v)


main()
