# A nested class has no bare-name spelling for `cls` to normalize to, so
# @classmethod on one is not supported yet.
from tpy import Int32


class Outer:
    class Inner:
        def __init__(self, x: Int32):
            self.x = x

        @classmethod
        def make(cls) -> Int32:  # tpyc: error(/nested class is not yet supported/)
            return 1


def main() -> None:
    print(Outer.Inner.make())


main()
