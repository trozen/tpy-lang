# Deferred generic instance inference: type params resolved from method calls
from tpy import Int32

class Container[T]:
    val: T
    count: Int32

    def __init__(self) -> None:
        self.count = Int32(0)

    def set(self, val: T) -> None:
        self.val = val
        self.count = self.count + Int32(1)

    def get(self) -> T:
        return self.val

    def get_count(self) -> Int32:
        return self.count

def main() -> None:
    # Basic: set() constrains T = Int32
    c = Container()  # tpyc: type(/Container\[Int32\]/)
    c.set(Int32(10))
    x = c.get()
    print(x)
    print(c.get_count())

    # Void method before constraining call is OK
    c2 = Container()
    print(c2.get_count())  # returns Int32, no T dependency
    c2.set(Int32(42))
    print(c2.get())

main()
