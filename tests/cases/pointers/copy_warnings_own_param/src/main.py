# Test: copy warnings for Own[T] params in builtin methods
from tpy import Int32, copy

class Point:
    x: Int32
    y: Int32

def main() -> None:
    p: Point = Point()
    p.x = 1
    p.y = 2
    items: list[Point] = []

    # lvalue into container method — warns (implicit copy)
    items.append(p)           # tpyc: warning(/copies Point into owned storage/)
    items.insert(0, p)        # tpyc: warning(/copies Point into owned storage/)

    # lvalue into subscript assignment — warns
    items[0] = p              # tpyc: warning(/copies Point into container/)

    # copy() silences the warning
    items.append(copy(p))     # tpyc: ok
    items[0] = copy(p)        # tpyc: ok
    items.insert(0, copy(p))  # tpyc: warning(/unnecessary copy/)

    # rvalue — no warning needed
    items.append(Point())     # tpyc: ok
    items[0] = Point()        # tpyc: ok

    # value types — no warning
    nums: list[Int32] = []
    x: Int32 = 42
    nums.append(x)            # tpyc: ok
    nums[0] = x               # tpyc: ok

    print(len(items))

main()
