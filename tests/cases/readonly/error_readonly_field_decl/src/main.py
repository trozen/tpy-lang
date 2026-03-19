# readonly[T] field declarations: assignment rejected outside __init__
from tpy import readonly

class Config:
    name: readonly[str]
    value: int

    def __init__(self, name: str, value: int) -> None:
        self.name = name  # tpyc: ok
        self.value = value

    def try_mutate(self) -> None:
        self.name = "bad"  # tpyc: error(/Cannot assign to readonly field/)
