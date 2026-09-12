# Send[T] on a statically non-Send type is a resolve-time error.
from tpy import int32, Ptr, Send

def f(p: Send[Ptr[int32]]) -> None:  # tpyc: error(/'Ptr\[int32\]' is not Send/)
    pass
