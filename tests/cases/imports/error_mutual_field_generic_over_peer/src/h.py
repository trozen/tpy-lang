from tpy import Ptr


class Handle[T]:
    ptr: Ptr[T]

    def __init__(self) -> None:
        self.ptr = None
