# Error: try_parse expects a string, not an integer
from enum import Enum
from tpy import try_parse

class Color(Enum):
    Red = 0

def main() -> None:
    c = try_parse(Color, 123)  # tpyc: error(/must be a string/)

main()
