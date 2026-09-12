# `assert isinstance(v, (Alpha, Beta))` on a three-member union: the tuple
# form only tests membership, so `v` stays the full union afterwards.
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


def probe(v: Alpha | Beta | Gamma) -> int32:
    # A tuple isinstance extracts nothing: the holds-OR test emits bare and
    # the subject stays the variant.
    assert isinstance(v, (Alpha, Beta))
    return 0


def main() -> None:
    print(probe(Alpha(1)))


main()
