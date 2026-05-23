# A @nocopy+__del__ field cannot be initialized in the body, but
# initializing it via an RHS that needs a codegen temporary (e.g. a
# varargs call) forces a body assignment because the MIL has no
# place to declare temps. The diagnostic must surface this with the
# temp-rollback reason.
import math
from tpy import nocopy


@nocopy
class HypotResult:
    val: float

    def __init__(self, v: float) -> None:
        self.val = v

    def __del__(self) -> None:
        pass


class Holder:
    _r: HypotResult

    def __init__(self, x: float, y: float) -> None:
        self._r = HypotResult(math.hypot(x, y))  # tpyc: error(/varargs call|member initializer list/)


def main() -> None:
    h = Holder(3.0, 4.0)
    print(h._r.val)


main()
