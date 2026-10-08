# A walrus rebind of a tuple global holding a reference from a function body is
# refused as the scalar reference global's is: the global is a pointer slot
# bound at module init, and a slot aimed at a function's storage would dangle.


class Point:
    def __init__(self, x: int) -> None:
        self.x = x


pair = (Point(1), 2)


def replace() -> int:
    global pair
    return (pair := (Point(3), 4))[1]  # tpyc: error(/Cannot reassign global variable 'pair' of a tuple of reference types/)


print(replace())
