from typing import Final
from tpy import Int32
A: Final[tuple[Int32, Int32 | float]] = (1, 2)
def main() -> None:
    print(A[0])
main()
