# readonly[T] field declarations: assignment rejected outside __init__. The
# permitted __init__ write, and reading the field back, are pinned by
# tests/cases/readonly/readonly_field_decl.
from tpy import readonly

class Config:
    name: readonly[str]
    value: int

    def __init__(self, name: str, value: int) -> None:
        self.name = name
        self.value = value

    def try_mutate(self) -> None:
        self.name = "bad"  # tpyc: error(/Cannot assign to readonly field/)
