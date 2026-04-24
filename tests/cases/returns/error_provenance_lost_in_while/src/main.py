# While-loop counterpart to error_provenance_lost_in_loop: post-loop
# kill-fact intersection must apply at the TpyWhile site too.


class Point:
    def __init__(self, x: int) -> None:
        self.x = x


def clobber_param_prov_while(seed: Point) -> Point:
    p = seed
    i = 0
    while i < 3:
        local = Point(i)
        p = local
        i += 1
    return p  # tpyc: error(/Cannot return local or temporary/)
