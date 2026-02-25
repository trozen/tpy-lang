# __copy__ with extra parameters is an error
from __future__ import annotations
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32

    def __init__(self, fd: Int32):
        self.fd = fd


class Container:
    handle: Handle

    def __init__(self, handle: Own[Handle]):
        self.handle = handle

    def __copy__(self, other: Container) -> Own[Container]:  # tpyc: error(/__copy__ must take no parameters/)
        return Container(Handle(self.handle.fd))
