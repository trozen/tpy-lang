# @classmethod and @staticmethod are mutually exclusive.
from tpy import Int32


class P:
    @classmethod
    @staticmethod
    def make(cls) -> Int32:  # tpyc: error(/cannot be combined with @staticmethod/)
        return 1


def main() -> None:
    print(P.make())


main()
