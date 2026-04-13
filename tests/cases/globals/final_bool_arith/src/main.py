# Final[bool] with logical and comparison ops between Finals and literals.
from typing import Final

T: Final[bool] = True
F: Final[bool] = False
AND_: Final[bool] = T and F
OR_: Final[bool] = T or F
EQ: Final[bool] = T == F
NE: Final[bool] = T != F

def main() -> None:
    print(AND_, OR_, EQ, NE)

main()
