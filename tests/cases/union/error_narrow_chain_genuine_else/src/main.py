# An `isinstance` if/elif chain over a union that ends in a genuine `else`:
# not lowered yet, so the case pins the reject.
from tpy import int32


class Alpha:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Beta:
    y: int32

    def __init__(self, y: int32) -> None:
        self.y = y


class Gamma:
    z: int32

    def __init__(self, z: int32) -> None:
        self.z = z


def probe(h: Alpha | Beta | Gamma | None) -> int32:
    if isinstance(h, Alpha):
        return h.x
    elif isinstance(h, Beta):  # tpyc: error(/if.narrow_shape/)
        return h.y
    else:
        # A real `else:` carries a non-member fact the narrow shape cannot
        # mirror, so the whole if rejects.
        return 0


def main() -> None:
    print(probe(Alpha(1)))


main()
