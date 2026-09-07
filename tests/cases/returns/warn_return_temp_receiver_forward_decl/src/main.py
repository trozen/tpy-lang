# Declaration order cannot change the Own-slot borrow verdict, and now nothing
# about the callee's body enters it: a borrow-returning call is a borrowed
# source at both owning slots below, so `Point` being declared AFTER the bodies
# that call it changes nothing. Same warnings as warn_return_temp_receiver_borrow
# with the declarations the other way round. The copy is the acknowledged
# CPython divergence, so main prints only what both sides agree on.
from tpy import Int32, Own, copy


class Wrapper:
    def build(self, n: Int32) -> Own['Point']:
        # Own RETURN slot, forward-declared callee.
        return Point(n).updated()  # tpyc: warning(/copies Point into owned storage/)

    def build_copy(self, n: Int32) -> Own['Point']:
        return copy(Point(n).updated())  # tpyc: ok

    def collect(self, n: Int32, xs: list['Point']) -> None:
        # Element slot, same forward-declared callee.
        xs.append(Point(n).updated())  # tpyc: warning(/copies Point into owned storage/)


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def updated(self) -> 'Point':
        self.x += 1
        return self


def main() -> None:
    w = Wrapper()
    p = w.build(1)
    xs: list[Point] = []
    w.collect(5, xs)
    # The appended element is the container's, not a detached copy.
    xs[0].x = 9
    print(p.x, xs[0].x, w.build_copy(3).x)


main()
