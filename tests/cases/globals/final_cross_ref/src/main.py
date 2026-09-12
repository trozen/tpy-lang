# Final globals that reference other Final globals
from typing import Final
from tpy import int32

BASE: Final[int32] = 10
ALIAS: Final[int32] = BASE

def main() -> None:
    print(BASE)
    print(ALIAS)

main()
