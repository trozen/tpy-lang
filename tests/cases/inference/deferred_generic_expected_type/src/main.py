# Deferred generic inference: resolved from expected type (param passing, return)
from tpy import Int32, Int64, Own

class Container[T]:
    val: T
    def __init__(self) -> None:
        pass
    def set(self, val: T) -> None:
        self.val = val
    def get(self) -> T:
        return self.val

class Pair[T, U]:
    a: T
    b: U
    def __init__(self) -> None:
        pass
    def get_a(self) -> T:
        return self.a
    def get_b(self) -> U:
        return self.b

def consume(c: Container[Int32]) -> None:
    c.set(Int32(99))
    print(c.get())

def make_container() -> Own[Container[Int64]]:
    c = Container()  # tpyc: type(/Container\[Int64\]/)
    return c

def setup_pair(p: Pair[Int32, Int64]) -> None:
    p.a = Int32(10)
    p.b = Int64(20)

def main() -> None:
    # Parameter passing resolves T
    c = Container()  # tpyc: type(/Container\[Int32\]/)
    consume(c)
    print(c.get())

    # Return type resolves T
    c2 = make_container()
    c2.set(Int64(42))
    print(c2.get())

    # Multi-param: parameter passing resolves T and U
    p = Pair()  # tpyc: type(/Pair\[Int32, Int64\]/)
    setup_pair(p)
    print(p.get_a())
    print(p.get_b())

    # Method calls before param passing (partial + expected-type)
    c3 = Container()  # tpyc: type(/Container\[Int32\]/)
    c3.set(Int32(7))
    consume(c3)
    print(c3.get())

    # Inline: no variable, resolved from param type
    consume(Container())

main()
