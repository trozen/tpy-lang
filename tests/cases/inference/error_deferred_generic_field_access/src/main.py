# Deferred generic inference: field access before type params resolved
from tpy import Int32

class Container[T]:
    val: T
    def __init__(self) -> None:
        pass
    def set(self, val: T) -> None:
        self.val = val

def main() -> None:
    c = Container()
    x = c.val  # tpyc: error(/Cannot access field/)

main()
