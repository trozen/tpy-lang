# Partial explicit type args on user-defined generic function.
from tpy import Int32, Own

class Box[T]:
    val: T
    def __init__(self, val: Own[T]) -> None:
        self.val = val

class Wrapper[A, B]:
    inner: A
    tag: B
    def __init__(self, inner: Own[A], tag: Own[B]) -> None:
        self.inner = inner
        self.tag = tag

def wrap_with_tag[A, B](inner: Own[A], tag: Own[B]) -> Own[Wrapper[A, B]]:
    return Wrapper[A, B](inner, tag)

def main() -> None:
    b = Box[Int32](Int32(42))
    # A=Box[Int32] explicit, B=Int32 inferred from second arg
    w = wrap_with_tag[Box[Int32]](b, Int32(99))
    print(w.inner.val)
    print(w.tag)
    print("done")

main()
