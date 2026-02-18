# Type inference through Own[T] with non-value-type containers (list).
from tpy import Int32, Own


class Point:
    x: Int32
    y: Int32


def first_val[T](items: Own[list[T]]) -> T:
    return items[0]


def consume_list[T](items: Own[list[T]]) -> None:
    pass


def consume_both[T](a: Own[T], b: Own[T]) -> None:
    pass


def main():
    # Infer T=Int32 through Own[list[T]] -- value element type
    nums: list[Int32] = [10, 20, 30]
    print(first_val(nums))

    # Infer T=Point through Own[list[T]] -- non-value element type
    pts: list[Point] = []
    p1 = Point()
    p1.x = 1
    p1.y = 2
    pts.append(p1)
    consume_list(pts)

    # Infer T=Point through two Own[T] params
    p2 = Point()
    p2.x = 5
    p2.y = 6
    p3 = Point()
    p3.x = 7
    p3.y = 8
    consume_both(p2, p3)

    print("done")


main()
