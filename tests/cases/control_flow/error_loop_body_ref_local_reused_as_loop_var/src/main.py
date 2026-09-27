# A reference-type local first bound in a loop body and read after a later
# `for` head's loop is the same local that head binds, so the reference-type
# rebind reject applies to it (rename the loop variable;
# BUGS.md#for-head-rebind-of-reference-local-rejected).


class Point:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


def f(pts: list[Point]) -> None:
    for i in range(1, 3):
        p = Point(i)
    # the head rebinds the body's reference-type local
    for p in pts:  # tpyc: error(/for-loop rebind of reference-type variable 'p'/)
        print(p.x)
    print(p.x)


f([Point(7)])
