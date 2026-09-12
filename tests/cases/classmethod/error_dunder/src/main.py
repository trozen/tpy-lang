# Dunder methods take an instance receiver, so none of them can be a
# classmethod.
from tpy import int32


class P:
    def __init__(self, x: int32):
        self.x = x

    @classmethod
    def __eq__(cls, other: "P") -> bool:  # tpyc: error(/cannot be a @classmethod/)
        return True


def main() -> None:
    print(P(1) == P(1))


main()
