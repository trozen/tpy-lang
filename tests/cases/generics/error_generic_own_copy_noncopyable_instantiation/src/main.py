# A generic body copying into an owning slot, instantiated at a NON-COPYABLE
# type: the located error the monomorphic twin reports, in place of the
# libstdc++ template spew the C++ build used to produce for the same program.
# `Pinned` is non-copyable because it defines `__del__`, which deletes the
# generated struct's copy constructor; `@nocopy` is the other way to be one
# (pinned by tpyc/test_compiler.py, since compilation stops at this error).
from tpy import Int32


class Pinned:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __del__(self) -> None:
        self.n = 0


def insert_slot[T](v: T) -> Int32:
    xs: list[T] = []
    # tpyc: error(/cannot copy non-copyable type 'Pinned' into owned storage/)
    xs.append(v)
    return len(xs)


def main() -> None:
    p = Pinned(3)
    print("noncopyable", insert_slot(p))


main()
