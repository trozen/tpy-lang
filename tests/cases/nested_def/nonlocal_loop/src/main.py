# Test nonlocal mutated inside a loop
from tpy import int32

def main() -> None:
    total: int32 = 0
    def add(x: int32) -> None:
        nonlocal total
        total += x
    items = [1, 2, 3, 4, 5]
    for item in items:
        add(item)
    print(total)

main()
