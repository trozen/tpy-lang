# A __del__ record that defines __copy__ is copyable in C++, so repeating it
# is allowed (the gate must respect the __copy__ escape hatch). The __copy__
# body is side-effect-free so output matches CPython, which aliases instead
# of copying (the documented repeat divergence) -- reads only.
from tpy import int32, Own


class Res:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __copy__(self) -> Own['Res']:
        return Res(self.v)

    def __del__(self) -> None:
        pass


def main():
    rs = [Res(7)] * 3
    print(len(rs), rs[0].v, rs[2].v)


main()
