# Local variables can shadow Final globals
from typing import Final
from tpy import Int32

X: Final[Int32] = 42

def main() -> None:
    X: Int32 = 99
    print(X)

main()
