from typing import Optional
from tpy import Int32

class H:
    ol: Optional[list[Int32]]
    def __init__(self) -> None:
        self.ol = [1, 2]

def f(h: H) -> Int32:
    l: list[Int32] = []
    match h.ol:
        case None:
            pass
        case l:
            l.append(1)
    return len(l)

def main() -> None:
    print(f(H()))

main()
