# `Base.m(self)` whose `self` is a comprehension target is no call on the
# receiver: refused with a message naming the non-receiver `self`, never rendered as
# `this->Base::m()`.
from tpy import int32


class Base:
    def __init__(self, x: int32) -> None:
        self.x = x

    def num(self) -> int32:
        return self.x


class Child(Base):
    def __init__(self, x: int32) -> None:
        super().__init__(x)

    def read_all(self, ps: list[Base]) -> list[int32]:
        # The receiver-spelled call through the hidden `self`.
        return [Base.num(self) for self in ps]  # tpyc: error(/passes a 'self' bound inside this method .*not the method's receiver/)


def main() -> None:
    print(Child(70).read_all([Base(2), Base(3)]))


main()
