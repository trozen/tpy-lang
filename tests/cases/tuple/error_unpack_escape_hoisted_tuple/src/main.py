# An unpack target that outlives its loop-scoped source tuple hoists the
# source and warns, as its twin `a = t[0]` does; the hoisted tuple is then
# refused by the lowering for both spellings (BUGS.md#hoisted-tuple-source-rejects).
from tpy import int32, Own


class P:
    def __init__(self, x: int32) -> None:
        self.x = x


def make_pair(i: int32) -> Own[tuple[P, int32]]:
    return (P(i), i)


def escape_local_unpack() -> int32:
    a = P(0)
    k = 0
    for i in range(2):
        t = make_pair(i)  # tpyc: error(/not yet supported by C\+\+ code generation/)
        a, k = t  # tpyc: warning(/'a' will not keep the object it was given -- 't' is rebound on each iteration and both names share its storage; use copy\(\) on the assigned value/)
    return a.x + k


def main() -> None:
    print(escape_local_unpack())


main()
