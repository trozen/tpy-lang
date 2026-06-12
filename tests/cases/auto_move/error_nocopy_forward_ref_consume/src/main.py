# @nocopy demotion through the forward-reference path: view is defined
# below the caller, and @nocopy forbids the copy fallback -- error.
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    vals: list[Int32]

    def __init__(self):
        self.vals = [5]


def main():
    h = Handle()
    v = view(h)
    close(h)  # tpyc: error(/@nocopy.*used after this point/)
    print(len(v))


def view(h: Handle) -> list[Int32]:
    return h.vals


def close(h: Own[Handle]) -> Int32:
    return len(h.vals)


main()
