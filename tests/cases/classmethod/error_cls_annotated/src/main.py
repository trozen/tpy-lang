# `cls` carries no type -- it names the defining class -- so annotating it is
# rejected (the slot a future `type[T]` would use).
from tpy import Int32


class P:
    def __init__(self, x: Int32):
        self.x = x

    @classmethod
    def make(cls: type) -> Int32:  # tpyc: error(/'cls' cannot be annotated/)
        return 1


def main() -> None:
    print(P.make())


main()
