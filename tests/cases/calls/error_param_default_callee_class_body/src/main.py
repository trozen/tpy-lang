# A default's constructor call names what the class body binds before the
# def, as CPython binds it there: a method, which no constant default may
# call (CPython would print 1079 1070, not tpy's int64 79 70).
from tpy import int32, int64


class C:
    def __init__(self) -> None:
        pass

    @staticmethod
    def int64(v: int32) -> int64:
        return int64(v + 100)

    def method(self, x: int64 = int64(7), y: int32 = 0) -> int64:  # tpyc: error(/must be a constant expression/)
        return x * 10 + y


def main() -> None:
    c = C()
    print(c.method(y=9), c.method())


main()
