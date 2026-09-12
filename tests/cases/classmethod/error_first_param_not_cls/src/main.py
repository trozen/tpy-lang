# A @classmethod's implicit first parameter must be spelled `cls`.
from tpy import int32


class P:
    def __init__(self, x: int32):
        self.x = x

    @classmethod
    def make(klass) -> int32:  # tpyc: error(/must be 'cls'/)
        return 1


def main() -> None:
    print(P.make())


main()
