# Final[int32] = -OTHER_FINAL: negation of another Final requires constexpr neg_check
from typing import Final
from tpy import int32, int64

A: Final[int32] = 5
NA: Final[int32] = -A
B: Final[int64] = -1000
NB: Final[int64] = -B

def main() -> None:
    print(A, NA)
    print(B, NB)

main()
