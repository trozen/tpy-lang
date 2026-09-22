# A for-source list literal built from NAMED records: the literal is owned
# container storage, so each element is copied in (the warning says so) and
# mutation through the loop variable does not reach the named source. CPython
# aliases instead, which is why this case skips the parity phase -- the
# declared copy is the same rule a `list[Rec]` local's literal follows.
from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1


# the elements are reference-typed PARAMETERS: the literal still owns copies, so
# its element slot must not take the parameter's reference form
def over_params(p1: Box, p2: Box) -> None:
    for b in [p1, p2]:  # tpyc: warning(/copies Box into owned storage/)
        b.bump()
        print("param_loop", b.n)
    print("param_source", p1.n, p2.n)


def main() -> None:
    b1 = Box(1)
    b2 = Box(2)
    # the copy is declared here, not at the mutation below
    for b in [b1, b2]:  # tpyc: warning(/copies Box into owned storage/)
        b.bump()
        print("loop", b.n)
    print("source", b1.n, b2.n)
    over_params(b1, b2)


main()
