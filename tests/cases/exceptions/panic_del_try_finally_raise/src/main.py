# A __del__ whose try/except/finally has an always-raising finally: the finally
# runs on the try body's fall-through path, so the raise reaches the fail-fast
# wrap and aborts. The compile warning does not fire here -- it skips `try`
# subtrees (tracked in BUGS.md), so the abort is this shape's only signal.
from tpy import Int32


class Fussy:
    _id: Int32

    def __init__(self, id: Int32):
        self._id = id

    def __del__(self):
        try:
            print("del try", self._id)
        except ValueError:
            print("del handler")
        finally:
            print("del finally", self._id)
            raise ValueError("cleanup failed")


def main():
    f = Fussy(9)
    print("in main")


main()
