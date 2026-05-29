# generic *args iteration monomorphized with a REFERENCE element type: the
# loop var binds as the type param T (borrow form for a reference T); count
# via pass-through (member access on a bare/bounded T is a separate gap).
class Box:
    val: int
    def __init__(self, v: int) -> None:
        self.val = v

def count_all[T](*args: T) -> int:
    n = 0
    for _ in args:  # tpyc: ok
        n += 1
    return n

def main() -> None:
    print(count_all(Box(1), Box(2), Box(3)))
    print(count_all(1, 2))

main()
