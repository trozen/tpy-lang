# readonly[T] field: inherited readonly fields are also enforced. The
# permitted write from the declaring class's own __init__ is pinned by
# tests/cases/readonly/readonly_field_decl.
from tpy import readonly

class Base:
    name: readonly[str]

    def __init__(self, n: str) -> None:
        self.name = n

class Child(Base):
    extra: int

    def __init__(self, n: str, e: int) -> None:
        super().__init__(n)
        self.extra = e

    def try_mutate(self) -> None:
        self.name = "bad"  # tpyc: error(/Cannot assign to readonly field/)
