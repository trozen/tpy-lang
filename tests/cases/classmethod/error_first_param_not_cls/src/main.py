# A @classmethod's implicit first parameter must be spelled `cls`.
from tpy import Int32


class P:
    def __init__(self, x: Int32):
        self.x = x

    @classmethod
    def make(klass) -> Int32:  # tpyc: error(/must be 'cls'/)
        return 1


def main() -> None:
    print(P.make())


main()
