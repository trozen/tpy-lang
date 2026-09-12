# @property needs an instance receiver, so it cannot be a classmethod.
from tpy import int32


class P:
    @property
    @classmethod
    def value(cls) -> int32:  # tpyc: error(/@property cannot be combined with @classmethod/)
        return 1


def main() -> None:
    p = P()
    print(p.value)


main()
