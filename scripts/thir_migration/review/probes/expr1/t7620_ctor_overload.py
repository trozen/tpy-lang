from typing import overload
from tpy import int32
class Rec:
    n: int32
    @overload
    def __init__(self) -> None: ...
    @overload
    def __init__(self, n: int32) -> None: ...
    def __init__(self, n: int32 = 0) -> None:
        self.n = n
def f() -> int32:
    r = Rec(1)
    return r.n
def main() -> None:
    pass
main()
