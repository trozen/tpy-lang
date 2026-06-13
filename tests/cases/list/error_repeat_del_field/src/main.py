# A copyable-looking record with a __del__-bearing FIELD is transitively
# copy-deleted in C++; repeating it must be rejected like the direct case.
from tpy import Int32


class Res:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v

    def __del__(self) -> None:
        pass


class Wrap:
    res: Res

    def __init__(self, v: Int32) -> None:
        self.res = Res(v)


def main():
    ws = [Wrap(1)] * 2  # tpyc: error(/Cannot repeat an element of non-copyable type Wrap/)
    print(ws[0].res.v)


main()
