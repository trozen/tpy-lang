# Final[T] declaration, negative literals, usage in expressions and function args
from typing import Final
from tpy import int32, char

MAX_SIZE: Final[int32] = 100
NEG_VAL: Final[int32] = -42
PI: Final[float] = 3.14159
DEBUG: Final[bool] = True
DISABLED: Final[bool] = False
NAME: Final[str] = "hello"
LETTER: Final[char] = "A"

def twice(x: int32) -> int32:
    return x + x

def main() -> None:
    print(MAX_SIZE)
    print(NEG_VAL)
    print(PI)
    print(DEBUG)
    print(DISABLED)
    print(NAME)
    print(LETTER)
    # Use Final in expressions and as function argument
    print(twice(MAX_SIZE))
    y: int32 = MAX_SIZE + NEG_VAL
    print(y)

main()
