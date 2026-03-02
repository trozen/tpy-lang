# Error: explicit type arg on method doesn't satisfy bound
from tpy import Int32, Comparable

class Wrapper:
    def __init__(self):
        pass

class Box[T]:
    val: T

    def __init__(self, val: T):
        self.val = val

    def compare[U: Comparable](self, a: U, b: U) -> bool:
        return a < b

def main() -> None:
    b = Box[Int32](Int32(1))
    b.compare[Wrapper](Wrapper(), Wrapper())  # tpyc: error(/does not satisfy bound/)

main()
