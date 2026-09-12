from typing import Optional
from tpy import int32, ValueType

class P(ValueType):
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

def f(o: Optional[P]) -> str:
    match o:
        case None:
            return "none"
        case P(x=1):
            return "one"
        case _:
            return "o"

def main() -> None:
    print(f(P(1)))

main()
