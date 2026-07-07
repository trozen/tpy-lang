# A conditional override conditions the trait on type parameters, so it is
# meaningless on a non-generic class -- rejected rather than silently no-opped.
from tpy import Ptr, Int32, unsafe_send

@unsafe_send(if_params_send=True)
class A:  # tpyc: error(/require type parameters/)
    p: Ptr[Int32]

    def __init__(self, p: Ptr[Int32]) -> None:
        self.p = p
