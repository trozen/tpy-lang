# A Send-only conditional override (@unsafe_send(if_params_send=True), no if_params on @unsafe_sync)
# does NOT make the record Sync -- with a raw-Ptr field it stays structurally
# non-Sync. This is the Mutex[T] shape (Send-conditional, not Sync).
from tpy import int32, Ptr, unsafe_send, assert_sync

@unsafe_send(if_params_send=True)
class SendCell[T]:
    p: Ptr[T]

    def __init__(self, p: Ptr[T]) -> None:
        self.p = p


def main() -> None:
    assert_sync[SendCell[int32]]()  # tpyc: error(/SendCell\[int32\] is not Sync/)


main()
