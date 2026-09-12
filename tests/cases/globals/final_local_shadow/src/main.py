# Local variables can shadow Final globals
from typing import Final
from tpy import int32

X: Final[int32] = 42

def main() -> None:
    X: int32 = 99
    print(X)

main()
