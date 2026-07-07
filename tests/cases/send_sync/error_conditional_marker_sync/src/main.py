# Sync-side of the marker-base vs conditional-override conflict: the `class
# Foo(Sync)` opt-in (unconditional structural claim) and a conditional
# @unsafe_sync(if_params_...) conflict on the same trait.
from tpy import Ptr, Sync, unsafe_sync

@unsafe_sync(if_params_send=True, if_params_sync=True)
class A[T](Sync):  # tpyc: error(/conditional @unsafe_sync.*conflicts with the 'Sync' base class/)
    p: Ptr[T]

    def __init__(self, p: Ptr[T]) -> None:
        self.p = p
