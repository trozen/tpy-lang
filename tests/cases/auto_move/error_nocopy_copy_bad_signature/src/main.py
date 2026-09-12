# __copy__ with wrong return type or extra parameters
from tpy import int32, nocopy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32):
        self.fd = fd


class BadReturn:
    handle: Handle

    def __init__(self, handle: Handle):
        self.handle = handle

    def __copy__(self) -> int32:  # tpyc: error(/__copy__ must return BadReturn/)
        return self.handle.fd
