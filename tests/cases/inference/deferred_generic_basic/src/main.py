# Deferred generic instance inference: type params resolved from method calls
from tpy import int32

class Container[T]:
    val: T
    count: int32

    def __init__(self) -> None:
        self.count = int32(0)

    def set(self, val: T) -> None:
        self.val = val
        self.count = self.count + int32(1)

    def get(self) -> T:
        return self.val

    def get_count(self) -> int32:
        return self.count

def main() -> None:
    # Basic: set() constrains T = int32
    c = Container()  # tpyc: type(/Container\[int32\]/)
    c.set(int32(10))
    x = c.get()
    print(x)
    print(c.get_count())

    # Void method before constraining call is OK
    c2 = Container()
    print(c2.get_count())  # returns int32, no T dependency
    c2.set(int32(42))
    print(c2.get())

main()
