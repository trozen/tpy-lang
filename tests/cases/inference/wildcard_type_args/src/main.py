# _ wildcard type arguments: partial explicit + inference in functions, constructors, methods
from tpy import int32, int64

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

def take_box(b: Box[int32]) -> None:
    print(b.get())

def main() -> None:
    # Function calls: _ is equivalent to omitting (full inference)
    r1 = identity[_](int32(10))  # tpyc: type(int32)
    print(r1)

    # Function calls: partial explicit + wildcard
    r2 = pair_func[_, int64](int32(5), int64(20))  # tpyc: type(int32)
    print(r2)

    r3 = pair_func[int32, _](int32(5), int64(20))  # tpyc: type(int32)
    print(r3)

    # Triple: wildcard in middle position
    r4 = triple[int32, _, int64](int32(1), int64(2), int64(3))  # tpyc: type(int64)
    print(r4)

    # Constructor: wildcard with init args
    b1 = Box[_](int32(42))  # tpyc: type(/Box\[int32\]/)
    print(b1.get())

    # Constructor: multi-param wildcard
    p1 = Pair[_, int64](int32(10), int64(20))  # tpyc: type(/Pair\[int32, int64\]/)
    print(p1.a)
    print(p1.b)

    # Constructor: no-init, full explicit (baseline)
    c = Container[int32]()
    c.set(int32(99))
    print(c.get())

    # Inline constructor with wildcard, resolved from param type
    take_box(Box[_](int32(7)))

    # Method call: wildcard on method-level type params
    m = Mapper[int32](int32(5))
    r5 = m.transform[_, int64](int32(1), int64(100))  # tpyc: type(int64)
    print(r5)

    # String literal: wildcard should produce str (same as full inference)
    s = identity[_]("hello")  # tpyc: type(str)
    print(s)

main()
