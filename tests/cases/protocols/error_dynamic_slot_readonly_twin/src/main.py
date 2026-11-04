# An @auto_readonly method filling a non-readonly @dynamic slot is rejected
# per docs/LANGUAGE_FEATURES.md "Receiver forms must match the slots": its
# readonly form fills no slot, so a call through it would skip dispatch.
from typing import Protocol
from tpy import dynamic, readonly, auto_readonly


class Ray:
    def __init__(self, d: float) -> None:
        self.d = d


@dynamic
class Surface(Protocol):
    def bounce(self, ray: Ray) -> float: ...


class Mat(Surface):
    def __init__(self) -> None:
        pass

    # The pair's readonly form has no slot to fill.
    @auto_readonly
    def bounce(self, ray: Ray) -> float:  # tpyc: error(/'Mat\.bounce' has a readonly form, but the 'bounce' it fills in @dynamic protocol 'Surface' is not readonly/)
        return ray.d


class Chrome(Mat):
    def bounce(self, ray: Ray) -> float:
        ray.d += 1.0
        return ray.d


def trace(m: Mat, r: Ray) -> float:
    return m.bounce(r)


def peek(m: readonly[Mat], r: Ray) -> float:
    return m.bounce(r)


def main() -> None:
    r = Ray(2.0)
    print(trace(Mat(), r), peek(Mat(), r), trace(Chrome(), r), r.d)


main()
