# Panic: Color["Purple"] should panic on invalid name
from enum import Enum

class Color(Enum):
    Red = 0

def main() -> None:
    c = Color["Purple"]

main()
