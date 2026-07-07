# Conditional Send/Sync override (@unsafe_send / @unsafe_sync + if_params_*): a generic
# record that is structurally non-Send (raw Ptr field) is Send/Sync exactly when
# its type parameter satisfies the listed markers. This is the mechanism Arc
# uses -- "Send + Sync iff T is Send + Sync" -- and the shape Mutex[T] will reuse.
from tpy import Int32, Ptr, unsafe_send, unsafe_sync, assert_send, assert_sync

# Send + Sync iff T is Send AND Sync (Arc's rule), despite the raw Ptr field.
@unsafe_send(if_params_send=True, if_params_sync=True)
@unsafe_sync(if_params_send=True, if_params_sync=True)
class Shared[T]:
    p: Ptr[T]

    def __init__(self, p: Ptr[T]) -> None:
        self.p = p

# Send iff T is Send (the Mutex[T] shape); Sync stays structural -- a raw-Ptr
# field is not Sync and there is no if_params on @unsafe_sync, so SendCell is never Sync.
@unsafe_send(if_params_send=True)
class SendCell[T]:
    p: Ptr[T]

    def __init__(self, p: Ptr[T]) -> None:
        self.p = p


def main() -> None:
    # Int32 and bool are both Send + Sync, so the conditional grants the trait.
    assert_send[Shared[Int32]]()
    assert_sync[Shared[Int32]]()
    assert_send[Shared[bool]]()
    assert_sync[Shared[bool]]()
    # Send-only conditional: granted for a Send T, structural (non-Sync) otherwise.
    assert_send[SendCell[Int32]]()
    print("ok")


main()
