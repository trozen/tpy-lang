from typing import Optional
from tpy import int32

class H:
    ol: Optional[list[int32]]
    def __init__(self) -> None:
        self.ol = [1, 2]

def f(h: H) -> int32:
    l: list[int32] = []
    match h.ol:
        case None:
            pass
        case l:
            l.append(1)
    return len(l)

def main() -> None:
    print(f(H()))

main()
