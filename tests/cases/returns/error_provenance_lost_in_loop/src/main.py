# Reassigning a param-aliasing local to a body-scoped value inside a loop
# must clear its param provenance -- the post-loop return should fail.


class Point:
    def __init__(self, x: int) -> None:
        self.x = x


def clobber_param_prov(seed: Point) -> Point:
    p = seed
    for i in range(3):
        local = Point(i)
        p = local
    return p  # tpyc: error(/Cannot return local or temporary/)
