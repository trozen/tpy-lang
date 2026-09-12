# Record constructor hint propagation: constructor arg type flows to inner call.
from tpy import int32, Own

class Box[T]:
    val: T
    def __init__(self, val: Own[T]) -> None:
        self.val = val

class Holder:
    box: Box[int32]
    def __init__(self, box: Own[Box[int32]]) -> None:
        self.box = box

def wrap[T](v: T) -> Own[Box[T]]:
    return Box[T](v)

def main() -> None:
    # Non-generic record constructor: hint from __init__ param flows to inner call
    h = Holder(wrap(int32(42)))
    print(h.box.val)

    # Same with bare literal: hint chain infers int32, coerces literal
    h2 = Holder(wrap(99))
    print(h2.box.val)

    # Generic record with explicit type args: hint flows to inner call
    outer = Box[Box[int32]](wrap(int32(10)))
    print(outer.val.val)

    # Same with bare literal
    outer2 = Box[Box[int32]](wrap(20))
    print(outer2.val.val)

    # Annotation-driven: no explicit type args on Box(), inferred from LHS
    outer3: Box[Box[int32]] = Box(wrap(30))
    print(outer3.val.val)
    print("done")

main()
