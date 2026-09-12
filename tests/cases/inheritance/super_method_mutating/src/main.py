# A method whose only mutation is via super().method() must NOT be auto-inferred
# as @readonly: the propagated transitive mutation through the super target has
# to mark the caller's self as mutated. Covers single- and multi-base.
from tpy import int32


class Base:
    x: int32

    def __init__(self) -> None:
        self.x = 0

    def bump(self) -> None:
        self.x = self.x + 1

    def get(self) -> int32:
        return self.x


class Child(Base):
    def __init__(self) -> None:
        super().__init__()

    def call_super_bump(self) -> None:
        super().bump()

    def call_super_get(self) -> int32:
        return super().get()


class Other:
    y: int32

    def __init__(self) -> None:
        self.y = 0

    def bump_other(self) -> None:
        self.y = self.y + 10


class Multi(Child, Other):
    def __init__(self) -> None:
        Child.__init__(self)
        Other.__init__(self)

    def call_super_bump_other(self) -> None:
        super().bump_other()


def main() -> None:
    c = Child()
    c.call_super_bump()
    c.call_super_bump()
    print(c.call_super_get())

    m = Multi()
    m.call_super_bump()
    m.call_super_bump_other()
    print(m.x)
    print(m.y)


main()
