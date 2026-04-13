# Final[Int32] = -OTHER_FINAL: negation of another Final requires constexpr neg_check
from typing import Final
from tpy import Int32, Int64

A: Final[Int32] = 5
NA: Final[Int32] = -A
B: Final[Int64] = -1000
NB: Final[Int64] = -B

def main() -> None:
    print(A, NA)
    print(B, NB)

main()
