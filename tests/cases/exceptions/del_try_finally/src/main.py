# A try/finally inside __del__ (a different throw-emitting path than `with`)
# must also compile: its finally rethrow lands in a noexcept destructor and
# is caught by the whole-body wrap. The finally body runs at drop time.
from tpy import int32


class Logged:
    _id: int32

    def __init__(self, id: int32):
        self._id = id

    def __del__(self):
        try:
            print("body", self._id)
        finally:
            print("finally", self._id)


def main():
    x = Logged(5)
    print("before drop")


main()
print("done")
