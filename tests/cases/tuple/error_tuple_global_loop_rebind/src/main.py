# A tuple global rebound inside a module-level loop from a value that parks
# (a call handing back an object): the site's one static would be overwritten
# per iteration, and the alias-rebind pass models no tuple site yet, so a name
# bound from an earlier iteration would read it with no warning where the
# scalar global gets one -- refused until then
# (BUGS.md#tuple-global-loop-rebind-unwarned). A rebind that only re-points
# names (`P = (W, i)`) parks nothing and compiles.
from tpy import int32, Own


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


V = Box(2)
M = make_mixed(V)
saved = M
for i in range(3):
    M = make_mixed(V)  # tpyc: error(/not yet supported/)
    if i == 0:
        saved = M
print(saved[0].n, M[0].n)
