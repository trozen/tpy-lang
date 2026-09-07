# `assert isinstance(v, (Alpha, Beta))` on a three-member union: the tuple
# form only tests membership, so `v` stays the full union afterwards.
from tpy import Int32


class Alpha:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class Beta:
    y: Int32

    def __init__(self, y: Int32) -> None:
        self.y = y


class Gamma:
    z: Int32

    def __init__(self, z: Int32) -> None:
        self.z = z


def probe(v: Alpha | Beta | Gamma) -> Int32:
    # A tuple isinstance extracts nothing: the holds-OR test emits bare and
    # the subject stays the variant.
    assert isinstance(v, (Alpha, Beta))
    return 0


def main() -> None:
    print(probe(Alpha(1)))


main()
