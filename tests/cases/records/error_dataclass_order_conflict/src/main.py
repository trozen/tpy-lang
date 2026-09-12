# Error: order=True with user-defined comparison method
from dataclasses import dataclass
from tpy import int32

@dataclass(order=True)
class Bad:  # tpyc: error(/cannot overwrite '__lt__'/)
    x: int32
    y: int32
    def __lt__(self, other: Bad) -> bool:
        return self.x < other.x

def main() -> None:
    pass

main()
