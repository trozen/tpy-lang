# Panic: stepped slice assignment with wrong RHS length.
from tpy import Int32

def main() -> None:
    a: list[Int32] = [1, 2, 3, 4, 5]
    a[::2] = [10, 20]  # slice selects 3 elements, RHS has 2

main()
