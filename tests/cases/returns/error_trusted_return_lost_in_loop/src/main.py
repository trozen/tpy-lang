# Reassigning a trusted-call-return local to a body-scoped value inside a
# loop must clear its trusted status -- the post-loop return should fail.


class Point:
    def __init__(self, x: int) -> None:
        self.x = x


def trusted(p: Point) -> Point:
    return p


def clobber_in_loop(seed: Point) -> Point:
    p = trusted(seed)
    for i in range(3):
        local = Point(i)
        p = local
    return p  # tpyc: error(/Cannot return local or temporary/)
