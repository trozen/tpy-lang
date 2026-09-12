# A user-defined record with a `Ptr[T] = None` field default must lower to
# `T* p = nullptr;` -- not `T* p = std::nullopt;` (which doesn't compile).
# Guards the codegen path for None on a raw-pointer field type.
from tpy import int32, Ptr


class Counter:
    x: int32 = 0


class Slot:
    p: Ptr[Counter] = None  # tpyc: ok
    tag: int32 = 0


def main() -> None:
    s = Slot()
    if s.p is None:
        print("null")
    print(s.tag)


main()
