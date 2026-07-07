# Cross-trait failure: explaining why Shared[list[Int32]] is not Send. list is
# Send but not Sync, and Shared's Send bound also requires Sync -- so the chain
# names the marker the argument lacks even though it satisfies the trait asked.
from tpy import Int32, Ptr, unsafe_send, unsafe_sync, assert_send

@unsafe_send(if_params_send=True, if_params_sync=True)
@unsafe_sync(if_params_send=True, if_params_sync=True)
class Shared[T]:
    p: Ptr[T]

    def __init__(self, p: Ptr[T]) -> None:
        self.p = p


def main() -> None:
    assert_send[Shared[list[Int32]]]()  # tpyc: error(/Shared\[list\[Int32\]\] is not Send/)


main()
