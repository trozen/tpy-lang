# Test: copy warnings for Own[T] params in StaticList builtin methods (parity with list)
from tpy import Int32, copy, StaticList

class Point:
    x: Int32
    y: Int32

def main() -> None:
    p: Point = Point()
    p.x = 1
    p.y = 2
    items: StaticList[Point, 10] = StaticList[Point, 10]()

    # lvalue into container method -- warns (implicit copy)
    items.append(p)           # tpyc: warning(/copies Point into owned storage/)
    items.insert(0, p)        # tpyc: warning(/copies Point into owned storage/)

    # copy() silences the warning
    items.append(copy(p))     # tpyc: ok
    items.insert(0, copy(p))  # tpyc: warning(/unnecessary copy/)

    # rvalue -- no warning needed
    items.append(Point())     # tpyc: ok

    # value types -- no warning
    nums: StaticList[Int32, 10] = StaticList[Int32, 10]()
    x: Int32 = 42
    nums.append(x)            # tpyc: ok

    print(len(items))

main()
