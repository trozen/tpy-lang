# Plain class with neither __hash__ nor __eq__ as set element -- exercises
# the "missing __hash__ and __eq__; ... define them explicitly" branch.
from tpy import int32


class Bare:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def main() -> None:
    s: set[Bare] = set()  # tpyc: error(/Bare.*cannot be used as a set element.*missing __hash__ and __eq__.*define them explicitly/)
    s.add(Bare(1))
    print(len(s))


main()
