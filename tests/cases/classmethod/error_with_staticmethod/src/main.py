# @classmethod and @staticmethod are mutually exclusive.
from tpy import int32


class P:
    @classmethod
    @staticmethod
    def make(cls) -> int32:  # tpyc: error(/cannot be combined with @staticmethod/)
        return 1


def main() -> None:
    print(P.make())


main()
