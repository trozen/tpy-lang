# Deferred generic inference: passing pending type as function argument
from tpy import Int32

class Container[T]:
    val: T
    def __init__(self) -> None:
        pass

def consume(c: Container[Int32]) -> None:
    pass

def main() -> None:
    c = Container()
    consume(c)  # tpyc: error(/unresolved type/)

main()
