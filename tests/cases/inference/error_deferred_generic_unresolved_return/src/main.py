# Deferred generic inference: method returning unresolved T before any constraining call
from tpy import int32

class Container[T]:
    val: T
    def __init__(self) -> None:
        pass
    def get(self) -> T:
        return self.val

def main() -> None:
    c = Container()
    x = c.get()  # tpyc: error(/Cannot determine return type/)

main()
