# @nocopy class cannot define __copy__ (contradictory)
from tpy import int32, Own, nocopy


@nocopy
class Handle:  # tpyc: error(/@nocopy class 'Handle' cannot define __copy__/)
    fd: int32

    def __init__(self, fd: int32):
        self.fd = fd

    def __copy__(self) -> Own[Handle]:
        return Handle(self.fd)
