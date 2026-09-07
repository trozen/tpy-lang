# A ternary whose condition is an `isinstance` test and whose arms are
# RECORDS: TPy lowers the scalar-result form only, so this pins the reject.
from tpy import Int32


class Alpha:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class Beta:
    y: Int32

    def __init__(self, y: Int32) -> None:
        self.y = y


def choose(p: Alpha | Beta, a: Alpha, a2: Alpha) -> Int32:
    # A RECORD-result ternary over an isinstance test: the arms carry
    # family-specific renders and the narrowed ternary is scalar-only.
    r = a if isinstance(p, Alpha) else a2  # tpyc: error(/ifexpr.narrow_result/)
    return r.x


def main() -> None:
    print(choose(Alpha(1), Alpha(2), Alpha(3)))


main()
