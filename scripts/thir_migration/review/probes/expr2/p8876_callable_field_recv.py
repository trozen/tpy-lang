from tpy import Int32
from typing import Callable
class H:
    cb: Callable[[Int32], Int32]
    def __init__(self, cb: Callable[[Int32], Int32]) -> None:
        self.cb = cb
class W:
    h: H
    def __init__(self, h: H) -> None:
        self.h = h
    def run(self) -> Int32:
        return self.h.cb(2)
def main() -> None:
    w = W(H(lambda x: x + 1))
    print(w.run())
    hs = [H(lambda x: x * 3)]
    print(hs[0].cb(2))
main()
