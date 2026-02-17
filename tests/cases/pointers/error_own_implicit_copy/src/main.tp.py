from tpy import Int32, Own


class Point:
    x: Int32
    y: Int32


def take_point(p: Own[Point]) -> Int32:
    return 42


def use_point(p: Point) -> None:
    print(p.x)


def main():
    p: Point = Point()
    p.x = 10
    # Passing Point to Own[Point] would be implicit copy -- p is used after
    result: Int32 = take_point(p)  # tpyc: error(/implicit copy/)
    use_point(p)
    print(result)


main()
