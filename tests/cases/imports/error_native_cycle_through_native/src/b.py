from tpy import int32, Ptr
import hub

class B:
    n: int32
    h: Ptr[hub.Hub]

    def __init__(self, n: int32, h: Ptr[hub.Hub]) -> None:
        self.n = n
        self.h = h
