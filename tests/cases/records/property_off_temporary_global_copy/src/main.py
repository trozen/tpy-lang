# A field write of a getter that lends a GLOBAL, read off a temporary, takes
# the warned copy its method spelling takes: the field no longer aliases G.
from tpy import Own, int32

G = [1, 2]


class H:
    tag: int32

    def __init__(self) -> None:
        self.tag = 0

    @property
    def items(self) -> list[int32]:
        return G


def mk() -> Own[H]:
    return H()


class Keeper:
    ys: list[int32]

    def __init__(self) -> None:
        # the field owns a copy of G (CPython aliases it): the warning declares it.
        self.ys = mk().items  # tpyc: warning(/copies list\[int32\] into field/)


def main() -> None:
    k = Keeper()
    k.ys.append(3)
    print("field_copy:", len(k.ys), len(G))


main()
