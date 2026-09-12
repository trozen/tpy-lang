# for-loop sibling of the while back-edge case: narrowing proven before
# the loop dies at body entry because the body may rebind the variable.
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32):
        self.x = x


def main():
    p: Point | None = Point(1)
    if p is None:
        return
    for i in range(3):
        print(p.x)  # tpyc: warning(/Potential None access/)
        if i == 1:
            p = None


main()
