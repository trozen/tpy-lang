# The opt-in marker base (class Foo(Send)) asserts an unconditional structural
# claim; a conditional @unsafe_send(if_params_...) asserts a per-type-param one. They
# conflict on the same trait.
from tpy import Ptr, Send, unsafe_send

@unsafe_send(if_params_send=True, if_params_sync=True)
class A[T](Send):  # tpyc: error(/conditional @unsafe_send.*conflicts with the 'Send' base class/)
    p: Ptr[T]

    def __init__(self, p: Ptr[T]) -> None:
        self.p = p
