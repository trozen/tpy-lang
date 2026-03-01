# Optional parameters with None as default value
from tpy import Int32
from typing import Optional

def find(items: list[Int32], target: Int32, default: Optional[Int32] = None) -> Optional[Int32]:
    for item in items:
        if item == target:
            return item
    return default

def main() -> None:
    items: list[Int32] = [Int32(10), Int32(20), Int32(30)]

    r1 = find(items, Int32(20))
    if r1 is not None:
        print(r1)

    r2 = find(items, Int32(99))
    if r2 is None:
        print("not found")

    r3 = find(items, Int32(99), Int32(-1))
    if r3 is not None:
        print(r3)

main()
