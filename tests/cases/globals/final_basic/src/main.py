# Final[T] declaration, negative literals, usage in expressions and function args
from typing import Final
from tpy import Int32, Char

MAX_SIZE: Final[Int32] = 100
NEG_VAL: Final[Int32] = -42
PI: Final[float] = 3.14159
DEBUG: Final[bool] = True
DISABLED: Final[bool] = False
NAME: Final[str] = "hello"
LETTER: Final[Char] = "A"

def twice(x: Int32) -> Int32:
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
    y: Int32 = MAX_SIZE + NEG_VAL
    print(y)

main()
