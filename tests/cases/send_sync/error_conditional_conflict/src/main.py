# A conditional override (@unsafe_send with if_params_*) and an unconditional one
# (@unsafe_send / @nosend) on the same trait of the same class contradict.
from tpy import Ptr, unsafe_send

@unsafe_send
@unsafe_send(if_params_send=True, if_params_sync=True)
class A[T]:  # tpyc: error(/conditional @unsafe_send.*conflicts with an unconditional/)
    p: Ptr[T]

    def __init__(self, p: Ptr[T]) -> None:
        self.p = p
