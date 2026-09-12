# Record printing with explicit __repr__ for bool, float, list, dict, tuple fields.
from tpy import int32, copy
from dataclasses import dataclass

@dataclass
class Config:
    flag: bool
    ratio: float
    items: list[int32]
    tags: dict[str, int32]
    pair: tuple[int32, str]

def main() -> None:
    c = Config(True, 3.14, [int32(1), int32(2)], {"a": int32(10)}, (int32(7), "ok"))
    print(c)
    empty: dict[str, int32] = {}
    c2 = Config(False, 1.0, [], empty, (int32(0), ""))
    print(c2)

main()
