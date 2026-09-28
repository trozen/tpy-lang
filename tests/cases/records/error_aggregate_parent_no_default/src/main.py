# An aggregate (a field without a default, so it inherits no `__init__`) over
# a parent needing arguments: `Child()` is rejected, citing the parent.
from tpy import int32


class Base:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Child(Base):
    tag: int32


def main() -> None:
    c = Child()  # tpyc: error(/Child\(\).*parent 'Base'/)


main()
