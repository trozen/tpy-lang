# A nested def's own `self` param inside an enum companion method is not the
# companion member: a lambda capturing it rejects like the plain-method twin.
from enum import Enum
from typing import Callable


class Color(Enum):
    RED = 1
    GREEN = 2

    def bump(self) -> int:
        def g(self: list[int]) -> int:
            # captures the nested def's list param, not the enum member
            h: Callable[[], int] = lambda: len(self)  # tpyc: error(/lambda\.self_capture/)
            self.append(5)
            return h()

        xs = [10]
        return g(xs)


def main() -> None:
    print(Color.RED.bump())


main()
