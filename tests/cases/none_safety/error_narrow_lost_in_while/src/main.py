# While-loop counterpart to error_narrow_lost_in_loop: post-loop kill-fact
# intersection for narrowed_types must apply at the TpyWhile site too.


class Point:
    def __init__(self, x: int) -> None:
        self.x = x


def clobber_narrow_while(seed: Point | None, fallback: Point) -> Point:
    x = seed
    if x is None:
        return fallback
    i = 0
    while i < 3:
        x = None
        i += 1
    return x  # tpyc: error(/Type mismatch/)
