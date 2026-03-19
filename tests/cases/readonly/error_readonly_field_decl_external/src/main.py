# readonly[T] field: external assignment also rejected
from tpy import readonly

class Config:
    name: readonly[str]
    value: int

    def __init__(self, name: str, value: int) -> None:
        self.name = name
        self.value = value

def external_mutate(c: Config) -> None:
    c.name = "bad"  # tpyc: error(/Cannot assign to readonly field/)
