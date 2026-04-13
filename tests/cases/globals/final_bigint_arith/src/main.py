# Final[int] (BigInt) with arithmetic between Finals and literals.
# BigInt Finals are runtime-initialized (extern const), so no compile-time
# overflow check -- but the initializer must still reduce to constant operands.
from typing import Final

A: Final[int] = 100
B: Final[int] = 7
SUM: Final[int] = A + B
PROD: Final[int] = A * B
NESTED: Final[int] = (A + B) * 10 - 1

def main() -> None:
    print(SUM)
    print(PROD)
    print(NESTED)

main()
