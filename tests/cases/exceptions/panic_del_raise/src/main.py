# A raise in __del__ warns at compile (a destructor cannot propagate) and
# fail-fasts at runtime -- the process aborts rather than continuing. CPython
# instead prints "Exception ignored in:" and continues, so this diverges by
# design (no_cpython). stdout up to the abort is flushed first.
from tpy import int32


class Fussy:
    _id: int32

    def __init__(self, id: int32):
        self._id = id

    def __del__(self):
        print("del", self._id)
        raise ValueError("cleanup failed")  # tpyc: warning(/.raise. in .__del__. cannot propagate/)


def main():
    f = Fussy(9)
    print("in main")


main()
