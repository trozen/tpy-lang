# An `Own[tuple[...]]` parameter rebound while a name still refers to an
# object it owns: the new element's object needs storage of its own, which a
# parameter does not have yet (the scalar `Own[Box]` parameter twin is
# BUGS.md#own-param-rebind-clobbers-alias) -- refused at the rebind, naming
# the alias.
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def pair(n: int32) -> Own[tuple[Box, Box]]:
    return (Box(n), Box(n + 1))


def own_param(p: Own[tuple[Box, Box]]) -> None:
    a = p[0]
    p = pair(9)  # tpyc: error(/'a' still refers to the object an element of 'p' was bound to, so the object the rebind of 'p' creates needs storage of its own/)
    a.n += 100
    print(a.n, p[0].n)


own_param(pair(1))
