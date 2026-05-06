from a import lookup
from enum import Enum

class Color(Enum):
    RED = 1
    BLUE = 2

def describe(c: Color) -> str:
    if c == Color.RED:
        return "red"
    return "blue"

# Real cycle edge: describe_default calls into a, which returns
# Color (defined here) -- both directions are exercised.
def describe_default() -> str:
    return describe(lookup())
