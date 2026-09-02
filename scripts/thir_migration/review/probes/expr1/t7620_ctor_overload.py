from typing import overload
from tpy import Int32
class Rec:
    n: Int32
    @overload
    def __init__(self) -> None: ...
    @overload
    def __init__(self, n: Int32) -> None: ...
    def __init__(self, n: Int32 = 0) -> None:
        self.n = n
def f() -> Int32:
    r = Rec(1)
    return r.n
def main() -> None:
    pass
main()
