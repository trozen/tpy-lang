# Test nonlocal for mutable captures
from tpy import Int32

def main() -> None:
    total: Int32 = 0
    def accumulate(x: Int32) -> None:
        nonlocal total
        total += x
    accumulate(10)
    accumulate(20)
    accumulate(30)
    print(total)

main()
