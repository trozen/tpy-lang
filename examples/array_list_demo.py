# ArrayList example: using the tplib generic list with fixed-capacity storage.
from tpy import Int32, Span, Array, copy
from tplib import ArrayList


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y


def sum_ints(lst: ArrayList[Int32, 16]) -> Int32:
    total: Int32 = 0
    for i in range(len(lst)):
        total = total + lst[i]
    return total


def print_points(lst: ArrayList[Point, 8]) -> None:
    for i in range(len(lst)):
        pt = lst[i]
        print(pt.x, pt.y)


def main() -> None:
    # --- Int32 ArrayList with capacity 16 ---
    nums = ArrayList[Int32, 16]()
    print(len(nums) == 0)        # True
    print(bool(nums))            # False

    nums.append(10)
    nums.append(20)
    nums.append(30)
    nums.append(40)
    print(len(nums))              # 4
    print(nums[0])                # 10
    print(nums[3])                # 40

    nums[1] = 99
    print(nums[1])                # 99

    print(nums.pop())             # 40
    print(len(nums))              # 3

    print(sum_ints(nums))         # 10 + 99 + 30 = 139

    nums.clear()
    print(len(nums) == 0)        # True
    print(bool(nums))            # False

    # --- Copy ---
    nums.append(1)
    nums.append(2)
    clone = copy(nums)
    clone[0] = 99
    print(nums[0])                # 1 (original unchanged)
    print(clone[0])               # 99

    # --- Point ArrayList with capacity 8 ---
    pts = ArrayList[Point, 8]()
    pts.append(Point(1, 2))
    pts.append(Point(3, 4))
    pts.append(Point(5, 6))
    print(len(pts))               # 3
    print_points(pts)             # 1 2 / 3 4 / 5 6

    p = pts.pop()
    print(p.x, p.y)              # 5 6
    print(len(pts))               # 2

    # --- Construct from Span ---
    arr: Array[Int32, 3] = [10, 20, 30]
    s: Span[Int32] = arr
    from_span = ArrayList[Int32, 8](s)
    print(len(from_span))            # 3
    print(from_span[0])              # 10
    print(from_span[2])              # 30
    
    # --- Construct from dict
    d = dict([("one", Int32(1)), ("two", Int32(2)), ("three", Int32(3))])
    from_dict = ArrayList[tuple[str, Int32], 16](d.items())
    for d_key in from_dict:
        print(d_key)
    print("from_dict:", from_dict)


main()
