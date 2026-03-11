# Error: type mismatch in slice assignment.
from tpy import Int32, Float64

def main() -> None:
    a: list[Int32] = [1, 2, 3]
    a[1:3] = [1.0, 2.0]  # tpyc: error(/incompatible with annotated element type/)

main()
