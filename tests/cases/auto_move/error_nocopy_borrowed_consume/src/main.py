# @nocopy demotion path: a live borrowing-call result (v borrows h)
# suppresses auto-move of h, and @nocopy forbids the copy fallback --
# the consume must error, not warn.
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    vals: list[Int32]

    def __init__(self):
        self.vals = [5]


def view(h: Handle) -> list[Int32]:
    return h.vals


def close(h: Own[Handle]) -> Int32:
    return len(h.vals)


def main():
    h = Handle()
    v = view(h)
    close(h)  # tpyc: error(/@nocopy.*used after this point/)
    print(len(v))


main()
