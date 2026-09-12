# A conditional override conditions the trait on type parameters, so it is
# meaningless on a non-generic class -- rejected rather than silently no-opped.
from tpy import Ptr, int32, unsafe_send

@unsafe_send(if_params_send=True)
class A:  # tpyc: error(/require type parameters/)
    p: Ptr[int32]

    def __init__(self, p: Ptr[int32]) -> None:
        self.p = p
