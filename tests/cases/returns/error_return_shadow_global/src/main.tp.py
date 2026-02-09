from tpy import Int32


class Point:
    x: Int32
    y: Int32


# Global with the same name as the local below
origin: Point = Point()


def get_origin() -> Point:
    origin: Point = Point()
    return origin  # tpyc: error(/Cannot return local or temporary/)
