# __copy__ with wrong return type or extra parameters
from tpy import Int32, nocopy


@nocopy
class Handle:
    fd: Int32

    def __init__(self, fd: Int32):
        self.fd = fd


class BadReturn:
    handle: Handle

    def __init__(self, handle: Handle):
        self.handle = handle

    def __copy__(self) -> Int32:  # tpyc: error(/__copy__ must return BadReturn/)
        return self.handle.fd
