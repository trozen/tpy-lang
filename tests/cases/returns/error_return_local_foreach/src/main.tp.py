from tpy import Int32


class Point:
    x: Int32
    y: Int32


def from_local_container() -> Point:
    local_pts: list[Point] = [Point(), Point()]
    best = local_pts[0]
    for p in local_pts:
        if p.x > best.x:
            best = p
    return best  # tpyc: error(/Cannot return local or temporary/)
