# A subclass temporary at a base reference parameter inside a lambda body:
# no statement hosts the storage held for it, so the call is rejected
# (BUGS.md#lambda-body-reference-argument-temp).
from typing import Callable
from typing import Protocol
from tpy import dynamic


@dynamic
class Surface(Protocol):
    def hit(self) -> str: ...


class Mat(Surface):
    hits: int

    def __init__(self) -> None:
        self.hits = 0

    def hit(self) -> str:
        self.hits += 1
        return "mat" + str(self.hits)


class Chrome(Mat):
    def hit(self) -> str:
        self.hits += 1
        return "chrome" + str(self.hits)


class Tracer:
    def __init__(self) -> None:
        pass

    def run(self, m: Mat) -> str:
        return m.hit()


def main() -> None:
    t = Tracer()
    f: Callable[[], str] = lambda: t.run(Chrome())  # tpyc: error(/lambda\.body.*method\.arg_shape/)
    print(f())


main()
