# Test nonlocal for mutable captures
from tpy import int32

def main() -> None:
    total: int32 = 0
    def accumulate(x: int32) -> None:
        nonlocal total
        total += x
    accumulate(10)
    accumulate(20)
    accumulate(30)
    print(total)

main()
