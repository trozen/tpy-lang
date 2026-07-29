# A borrow-form tuple global (an element of reference type) needs the var-decl
# path's storage lift, which the inline walrus assign has nowhere to put -- the
# same exclusion the walrus makes for a reference-typed local reassignment.


class Point:
    def __init__(self, x: int) -> None:
        self.x = x


pair = (Point(1), 2)


def replace() -> int:
    global pair
    return (pair := (Point(3), 4))[1]  # tpyc: error(/walrus reassignment of global 'pair'.*not supported yet/)


print(replace())
