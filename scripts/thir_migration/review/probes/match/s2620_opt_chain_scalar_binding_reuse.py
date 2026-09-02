from typing import Optional
from tpy import Int32

class Box:
    val: Optional[Int32]
    def __init__(self, val: Optional[Int32]) -> None:
        self.val = val

def f(o: Optional[Box], oi: Optional[Int32]) -> str:
    match oi:
        case 1:
            print("one")
        case w:
            print(w is None)
    match o:
        case Box(val=w):
            return str(w is None)
        case None:
            return "n"
    return "t"

def main() -> None:
    print(f(Box(3), 2))

main()
