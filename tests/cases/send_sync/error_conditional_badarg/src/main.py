# @unsafe_send / @unsafe_sync take only the if_params_send / if_params_sync
# keyword flags -- passing a marker positionally (a habit from bound-style
# syntax) is rejected.
from tpy import Ptr, Send, unsafe_send

@unsafe_send(Send)  # tpyc: error(/takes only the keyword arguments/)
class A[T]:
    p: Ptr[T]

    def __init__(self, p: Ptr[T]) -> None:
        self.p = p
