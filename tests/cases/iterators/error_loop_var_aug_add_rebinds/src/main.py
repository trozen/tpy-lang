# `v += 1` on a reference loop variable whose type defines only `__add__` is
# `v = v + 1`, a rebind of the loop variable, refused as the plain rebind is.
from tpy import Own, int32


class V:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, k: int32) -> Own["V"]:
        return V(self.n + k)


def bump_all(vs: list[V]) -> None:
    for v in vs:
        v += 1  # tpyc: error(/Cannot reassign loop variable 'v'/)


def main() -> None:
    vs = [V(1), V(2)]
    bump_all(vs)
    print(vs[0].n, vs[1].n)


main()
