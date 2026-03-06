from tpy import Int32
from tplib import ArrayList

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y


def test_value_types():
    # Value type on ArrayList - get_value (copy semantics)
    nums = ArrayList[Int32, 4]()
    nums.append(10)
    nums.append(20)
    val: Int32 = nums[0]
    print(val)  # 10

    # Modifying val doesn't affect nums[0] (value semantics)
    val = 99
    print(nums[0])  # Still 10

    # Test with list[T] as well
    int_list: list[Int32] = [5, 6, 7]
    v: Int32 = int_list[1]  # get_value for Int32 element
    print(v)  # 6


def test_object_types():
    # Object type on ArrayList - get_ref (reference semantics)
    points = ArrayList[Point, 4]()
    points.append(Point(1, 2))
    points.append(Point(3, 4))

    # Accessing object field through subscript
    print(points[0].x)  # 1

    # Modifying object through subscript reference
    points[0].x = 100
    print(points[0].x)  # 100

    # Test with list[Point] as well
    obj_list: list[Point] = [Point(10, 20)]
    obj_list[0].y = 200  # get_ref for Point element
    print(obj_list[0].y)  # 200


test_value_types()
test_object_types()
