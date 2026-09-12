# probe-args: --default-int BigInt
from enum import Enum
from tpy import int32
class Color(Enum):
    RED = 1
    BLUE = 2
def f() -> Color:
    return Color(1)
def main() -> None:
    pass
main()
