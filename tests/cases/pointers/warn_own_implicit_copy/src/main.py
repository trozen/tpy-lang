from tpy import int32, Own


class Point:
    x: int32
    y: int32


def take_point(p: Own[Point]) -> int32:
    return 42


def use_point(p: Point) -> None:
    print(p.x)


def main():
    p: Point = Point()
    p.x = 10
    # Passing Point to Own[Point] would be implicit copy -- p is used after
    result: int32 = take_point(p)  # tpyc: warning(/copies.*into owned storage/)
    use_point(p)
    print(result)


main()
