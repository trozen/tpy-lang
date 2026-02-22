# Final[int] with BigInt (uses const instead of constexpr)
from typing import Final

BIG_VALUE: Final[int] = 1000000

def main() -> None:
    print(BIG_VALUE)

main()
