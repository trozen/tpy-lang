# A conditional override only ever GRANTS the trait; when a type parameter
# fails a listed marker the record falls back to its (non-Send) structural
# answer, and the why-not chain names the offending parameter and marker.
from tpy import int32, Ptr, unsafe_send, unsafe_sync, assert_send

@unsafe_send(if_params_send=True, if_params_sync=True)
@unsafe_sync(if_params_send=True, if_params_sync=True)
class Shared[T]:
    p: Ptr[T]

    def __init__(self, p: Ptr[T]) -> None:
        self.p = p


def main() -> None:
    # Ptr[int32] is neither Send nor Sync -> the condition fails, and the chain
    # attributes it to the type parameter rather than the internal Ptr field.
    assert_send[Shared[Ptr[int32]]]()  # tpyc: error(/Shared\[Ptr\[int32\]\] is not Send/)


main()
