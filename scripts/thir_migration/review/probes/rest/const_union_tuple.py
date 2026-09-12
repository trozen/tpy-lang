from typing import Final
from tpy import int32
A: Final[tuple[int32, int32 | float]] = (1, 2)
def main() -> None:
    print(A[0])
main()
