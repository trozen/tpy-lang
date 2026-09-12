# @dataclass __post_init__ runs after the synthesized __init__ sets the fields,
# so it can derive a field from the others.
from dataclasses import dataclass
from tpy import int32

@dataclass
class Rect:
    w: int32
    h: int32
    area: int32 = 0

    def __post_init__(self) -> None:
        self.area = self.w * self.h

def main() -> None:
    r = Rect(3, 4)
    print(r.w)
    print(r.h)
    print(r.area)  # 12 -- derived by __post_init__; stays 0 if the hook never runs

main()
