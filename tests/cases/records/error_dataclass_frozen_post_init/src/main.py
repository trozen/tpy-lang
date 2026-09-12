# A __post_init__ that assigns a field of a frozen @dataclass is a compile error:
# frozen makes self readonly. (CPython raises FrozenInstanceError at runtime.)
from dataclasses import dataclass
from tpy import int32

@dataclass(frozen=True)
class C:
    x: int32
    y: int32 = 0

    def __post_init__(self) -> None:
        self.y = self.x * 2  # tpyc: error(/readonly|frozen|mutate/)

def main() -> None:
    c = C(3)
    print(c.y)

main()
