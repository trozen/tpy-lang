from tpy import Copyable, Ptr


class Handle[T: Copyable]:
    ptr: Ptr[T]

    def __init__(self) -> None:
        self.ptr = None
