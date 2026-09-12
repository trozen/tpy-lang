# `cls` binds statically to the DEFINING class, so reaching an inherited
# classmethod through a subclass would construct the base -- CPython would
# construct the subclass. Rejected instead of diverging silently.
from typing import Self

from tpy import int32, Own


class Base:
    def __init__(self, x: int32):
        self.x = x

    @classmethod
    def make(cls) -> Own[Self]:
        return cls(1)


class Child(Base):
    pass


def main() -> None:
    b = Child.make()  # tpyc: error(/inherited from 'Base'.*would construct a 'Base'/)
    print(b.x)


main()
