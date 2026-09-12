# A __del__-bearing record has deleted copy ops, so repeating it must be
# rejected in sema even with a list annotation (every repeat container copies).
from tpy import int32


class Res:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __del__(self) -> None:
        pass


def main():
    rs: list[Res] = [Res(7)] * 2  # tpyc: error(/Cannot repeat an element of non-copyable type Res/)
    print(rs[0].v)


main()
