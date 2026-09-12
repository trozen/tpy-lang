# A `with` (context manager) inside __del__ must compile: the CM's cleanup
# rethrow path lives in a C++ noexcept destructor, so the emitted `throw;`
# must be caught within the dtor (else -Werror=terminate). Observing the
# CM's __enter__/__exit__ output proves the with-body runs at drop time.
from tpy import int32


class Guard:
    _tag: int32

    def __init__(self, tag: int32):
        self._tag = tag

    def __enter__(self) -> int32:
        print("enter", self._tag)
        return self._tag

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("exit", self._tag)


class Resource:
    _id: int32

    def __init__(self, id: int32):
        self._id = id

    def __del__(self):
        with Guard(self._id) as t:
            print("cleanup", t)


def main():
    r = Resource(3)
    print("before drop")


main()
print("done")
