# Set methods: discard, remove, pop, clear, copy
from tpy import Int32

def main() -> None:
    s: set[Int32] = {10, 20, 30}

    # discard (no error if missing)
    s.discard(20)
    print(s)
    s.discard(99)  # no error
    print(s)

    # remove (panics if missing, tested separately)
    s.remove(10)
    print(s)

    # pop (removes first element)
    val: Int32 = s.pop()
    print(val)
    print(s)

    # copy
    a: set[Int32] = {1, 2, 3}
    b: set[Int32] = a.copy()
    b.add(4)
    print(a)
    print(b)

    # clear
    a.clear()
    print(a)
    print(len(a))

main()
