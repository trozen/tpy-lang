# Send[T] on a statically non-Send type is a resolve-time error.
from tpy import Int32, Ptr, Send

def f(p: Send[Ptr[Int32]]) -> None:  # tpyc: error(/'Ptr\[Int32\]' is not Send/)
    pass
