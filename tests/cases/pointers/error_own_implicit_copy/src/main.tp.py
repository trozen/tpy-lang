from tpy import Int32, Own


class Point:
    x: Int32
    y: Int32


def take_point(p: Own[Point]) -> Int32:
    return 42


def main():
    p: Point = Point()
    p.x = 10
    # Passing Point to Own[Point] would be implicit copy - should error
    result: Int32 = take_point(p)  # tpyc: error(/implicit copy/)
    print(result)


main()
