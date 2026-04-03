# Record printing with explicit __repr__ for bool, float, list, dict, tuple fields.
from tpy import Int32, copy
from dataclasses import dataclass

@dataclass
class Config:
    flag: bool
    ratio: float
    items: list[Int32]
    tags: dict[str, Int32]
    pair: tuple[Int32, str]

def main() -> None:
    c = Config(True, 3.14, [Int32(1), Int32(2)], {"a": Int32(10)}, (Int32(7), "ok"))
    print(c)
    empty: dict[str, Int32] = {}
    c2 = Config(False, 1.0, [], empty, (Int32(0), ""))
    print(c2)

main()
