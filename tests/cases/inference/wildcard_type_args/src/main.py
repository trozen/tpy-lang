# _ wildcard type arguments: partial explicit + inference in functions, constructors, methods
from tpy import Int32, Int64

def identity[T](x: T) -> T:
    return x

def pair_func[T, U](a: T, b: U) -> T:
    return a

def triple[A, B, C](a: A, b: B, c: C) -> B:
    return b

class Box[T]:
    val: T
    def __init__(self, val: T) -> None:
        self.val = val
    def get(self) -> T:
        return self.val

class Pair[T, U]:
    a: T
    b: U
    def __init__(self, a: T, b: U) -> None:
        self.a = a
        self.b = b

class Container[T]:
    val: T
    def __init__(self) -> None:
        pass
    def set(self, val: T) -> None:
        self.val = val
    def get(self) -> T:
        return self.val

class Mapper[T]:
    val: T
    def __init__(self, val: T) -> None:
        self.val = val
    def transform[U, V](self, u: U, v: V) -> V:
        return v

def take_box(b: Box[Int32]) -> None:
    print(b.get())

def main() -> None:
    # Function calls: _ is equivalent to omitting (full inference)
    r1 = identity[_](Int32(10))  # tpyc: type(Int32)
    print(r1)

    # Function calls: partial explicit + wildcard
    r2 = pair_func[_, Int64](Int32(5), Int64(20))  # tpyc: type(Int32)
    print(r2)

    r3 = pair_func[Int32, _](Int32(5), Int64(20))  # tpyc: type(Int32)
    print(r3)

    # Triple: wildcard in middle position
    r4 = triple[Int32, _, Int64](Int32(1), Int64(2), Int64(3))  # tpyc: type(Int64)
    print(r4)

    # Constructor: wildcard with init args
    b1 = Box[_](Int32(42))  # tpyc: type(/Box\[Int32\]/)
    print(b1.get())

    # Constructor: multi-param wildcard
    p1 = Pair[_, Int64](Int32(10), Int64(20))  # tpyc: type(/Pair\[Int32, Int64\]/)
    print(p1.a)
    print(p1.b)

    # Constructor: no-init, full explicit (baseline)
    c = Container[Int32]()
    c.set(Int32(99))
    print(c.get())

    # Inline constructor with wildcard, resolved from param type
    take_box(Box[_](Int32(7)))

    # Method call: wildcard on method-level type params
    m = Mapper[Int32](Int32(5))
    r5 = m.transform[_, Int64](Int32(1), Int64(100))  # tpyc: type(Int64)
    print(r5)

    # String literal: wildcard should produce str (same as full inference)
    s = identity[_]("hello")  # tpyc: type(str)
    print(s)

main()
