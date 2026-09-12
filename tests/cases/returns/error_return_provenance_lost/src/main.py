from tpy import int32


class Point:
    x: int32
    y: int32


def provenance_lost(points: list[Point]) -> Point:
    best = points[0]
    best = Point()
    return best  # tpyc: error(/Cannot return local or temporary/)
