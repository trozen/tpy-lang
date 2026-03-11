# Deferred generic inference: passing pending type to non-constraining parameter
from tpy import Int32

class Container[T]:
    val: T
    def __init__(self) -> None:
        pass

def consume_int(x: Int32) -> None:
    pass

def main() -> None:
    c = Container()
    consume_int(c)  # tpyc: error(/unresolved type/)

main()
