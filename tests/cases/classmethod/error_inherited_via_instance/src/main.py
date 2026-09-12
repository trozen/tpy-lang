# The instance-receiver spelling of the inherited-classmethod reject: `cls`
# binds statically to the DEFINING class, so this would construct a 'Base'
# where CPython constructs the subclass the receiver actually is.
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
    c = Child(5)
    d = c.make()  # tpyc: error(/inherited from 'Base'.*would construct a 'Base'/)
    print(d.x)


main()
