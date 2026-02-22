# Final globals that reference other Final globals
from typing import Final
from tpy import Int32

BASE: Final[Int32] = 10
ALIAS: Final[Int32] = BASE

def main() -> None:
    print(BASE)
    print(ALIAS)

main()
