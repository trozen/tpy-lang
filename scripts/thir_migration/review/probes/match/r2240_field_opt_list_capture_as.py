from typing import Optional
from tpy import Int32

class H:
    ol: Optional[list[Int32]]
    def __init__(self) -> None:
        self.ol = [1, 2]

def f(h: H) -> str:
    match h.ol:
        case None:
            return "none"
        case x as y:
            return str(len(y))

def main() -> None:
    print(f(H()))

main()
