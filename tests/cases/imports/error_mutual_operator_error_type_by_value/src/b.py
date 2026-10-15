from tpy import error_return, int32
from a import E


class Neg:
    def __init__(self, k: int32) -> None:
        self.k = k

    # The friend `operator-` returns `std::expected<..., E>`, which holds `E`
    # by value where `b.hpp` sees it only forward-declared.
    @error_return(E)
    def __neg__(self) -> int32:  # tpyc: error(/Cyclic import: error type/)
        if self.k < 0:
            raise E()
        return -self.k


def use() -> int32:
    try:
        return -Neg(2)
    except E:
        return 0
