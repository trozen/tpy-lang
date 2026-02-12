from tpy import Int32


class Point:
    x: Int32
    y: Int32


def find_max(points: list[Point]) -> Point:
    best = points[0]
    for p in points:
        if p.x > best.x:
            best = p
    return best  # tpyc: ok (best derives from param)


def get_first(points: list[Point]) -> Point:
    return points[0]  # tpyc: ok (param subscript)


def get_x(p: Point) -> Point:
    return p  # tpyc: ok (param directly)


def get_field_ref(p: Point) -> Point:
    r = p
    return r  # tpyc: ok (r derives from param)


def chained(points: list[Point]) -> Point:
    a = points[0]
    b = a
    return b  # tpyc: ok (chained provenance)


def both_branches(points: list[Point], flag: bool) -> Point:
    if flag:
        result = points[1]
    else:
        result = points[2]
    return result  # tpyc: ok (both branches from param)


def main():
    pts: list[Point] = [Point(), Point(), Point()]
    pts[0].x = 10
    pts[1].x = 30
    pts[2].x = 20

    best: Point = find_max(pts)
    print(best.x)

    # Mutation through returned reference is visible
    best.x = 99
    print(pts[1].x)

    first: Point = get_first(pts)
    print(first.x)

    c: Point = chained(pts)
    print(c.x)

    b: Point = both_branches(pts, True)
    print(b.x)


main()
