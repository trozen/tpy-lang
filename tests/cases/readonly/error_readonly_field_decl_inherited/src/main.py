# readonly[T] field: inherited readonly fields are also enforced
from tpy import readonly

class Base:
    name: readonly[str]

    def __init__(self, n: str) -> None:
        self.name = n  # tpyc: ok

class Child(Base):
    extra: int

    def __init__(self, n: str, e: int) -> None:
        super().__init__(n)
        self.extra = e

    def try_mutate(self) -> None:
        self.name = "bad"  # tpyc: error(/Cannot assign to readonly field/)
