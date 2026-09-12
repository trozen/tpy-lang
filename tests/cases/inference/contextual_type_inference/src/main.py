# Contextual type inference: expected type fills unresolved type params.
from tpy import int32, Own

class Container[T]:
    val: T

    def __repr__(self) -> str:
        return f"Container(val={self.val!r})"

class Pair[A, B]:
    first: A
    second: B

    def __init__(self, a: A) -> None:
        self.first = a

    def __repr__(self) -> str:
        return f"Pair(first={self.first!r}, second={self.second!r})"

def make_box[T]() -> Own[Container[T]]:
    return Container[T]()

def identity[T](x: T) -> T:
    return x

# Return context
def get_box() -> Own[Container[int32]]:
    return make_box()  # tpyc: ok

def main():
    # Assignment context (annotated var_decl)
    b: Container[int32] = make_box()  # tpyc: ok
    print(b)

    # Record constructor (no __init__, no args) with annotation context
    c: Container[int32] = Container()  # tpyc: ok
    print(c)

    print(get_box())

    # Partial inference: args determine T, context not needed
    y: int32 = identity(int32(5))  # tpyc: ok
    print(y)

    # Reassignment context: existing type used as hint
    r = Container[int32]()
    r = Container()  # tpyc: ok
    print(r)

    # Constructor with __init__: args infer some params, context infers the rest
    p: Pair[int32, str] = Pair(int32(42))  # tpyc: ok -- A from arg, B from context
    print(p)

    print("done")

main()
