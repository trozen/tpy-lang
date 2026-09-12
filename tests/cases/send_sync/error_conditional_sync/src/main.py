# Sync-side of the conditional override + the why_not_sync chain: Shared is
# Sync iff T is Send + Sync, so Shared[Ptr[int32]] is not Sync, and the chain
# attributes it to the type parameter (the conditional arm on the Sync side).
from tpy import int32, Ptr, unsafe_send, unsafe_sync, assert_sync

@unsafe_send(if_params_send=True, if_params_sync=True)
@unsafe_sync(if_params_send=True, if_params_sync=True)
class Shared[T]:
    p: Ptr[T]

    def __init__(self, p: Ptr[T]) -> None:
        self.p = p


def main() -> None:
    assert_sync[Shared[Ptr[int32]]]()  # tpyc: error(/Shared\[Ptr\[int32\]\] is not Sync/)


main()
