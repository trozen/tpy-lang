# @nocopy demotion through the forward-reference path: view is defined
# below the caller, and @nocopy forbids the copy fallback -- error.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    vals: list[int32]

    def __init__(self):
        self.vals = [5]


def main():
    h = Handle()
    v = view(h)
    close(h)  # tpyc: error(/@nocopy.*used after this point/)
    print(len(v))


def view(h: Handle) -> list[int32]:
    return h.vals


def close(h: Own[Handle]) -> int32:
    return len(h.vals)


main()
