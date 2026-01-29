"""Tests list[T]() constructor syntax."""
from tpy import Int32

def test_local_list() -> None:
    # Local list without annotation
    local_nums = list[Int32]()
    local_nums.append(100)
    local_nums.append(200)
    print(len(local_nums))

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32 = 0, y: Int32 = 0):
        self.x = x
        self.y = y

# Constructor without LHS annotation - type inferred from constructor
nums = list[Int32]()
nums.append(1)
nums.append(2)
print(len(nums))

# Constructor with matching LHS annotation
other: list[Int32] = list[Int32]()
other.append(3)
print(len(other))

# List of records without annotation (tests header ordering)
points = list[Point]()
points.append(Point(10, 20))
print(len(points))
print(points[0].x)

# Nested list of records (tests recursive record check)
nested = list[list[Point]]()
inner = list[Point]()
inner.append(Point(99, 88))
nested.append(inner)
print(len(nested))
print(nested[0][0].x)

# Test local list creation
test_local_list()
