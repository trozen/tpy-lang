# Optional parameters with None as default value
from tpy import int32
from typing import Optional

def find(items: list[int32], target: int32, default: Optional[int32] = None) -> Optional[int32]:
    for item in items:
        if item == target:
            return item
    return default

def main() -> None:
    items: list[int32] = [int32(10), int32(20), int32(30)]

    r1 = find(items, int32(20))
    if r1 is not None:
        print(r1)

    r2 = find(items, int32(99))
    if r2 is None:
        print("not found")

    r3 = find(items, int32(99), int32(-1))
    if r3 is not None:
        print(r3)

main()
