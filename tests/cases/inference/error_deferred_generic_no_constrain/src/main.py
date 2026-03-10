# Deferred generic inference: no constraining calls -- safety net error
from tpy import Int32

class Container[T]:
    val: T
    def __init__(self) -> None:
        pass

def main() -> None:
    c = Container()  # tpyc: error(/Cannot infer type argument/)

main()
