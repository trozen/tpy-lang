# No super() over `__init__`-less bases whose generic ancestor holds a
# @nocopy+__del__ field names it (LANGUAGE_FEATURES "Single class inheritance").
from tpy import int32, nocopy


@nocopy
class Res:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __del__(self) -> None:
        pass


class Holder[T]:
    item: T


class Base(Holder[Res]):
    pass


class Child(Base):
    extra: int32

    # the base cannot be default-constructed through its generic ancestor
    def __init__(self, e: int32) -> None:  # tpyc: error(/cannot be constructed without arguments \(ancestor 'Holder' \(inherited by 'Base'\)/)
        self.item = Res(e)
        self.extra = e


def main() -> None:
    c = Child(7)
    print(c.extra)


main()
