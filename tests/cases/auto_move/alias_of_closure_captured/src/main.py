# `xs = ys` at the last textual use of `ys` is a MOVE -- unless a nested def
# defined earlier captured `ys`: the closure reads that storage by reference at
# every later call, so the bind must alias it. The move used to be taken
# anyway (the closure then read the moved-from object). Each section calls the
# closure AFTER mutating through the alias.
from tpy import Own, int32


class Rec:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def mk() -> Own[list[int32]]:
    r = [1, 2, 3]
    r.append(4)
    return r


# container source, closure defined before the alias
def container() -> int32:
    ys = mk()

    def peek() -> int32:
        return len(ys)
    xs = ys  # tpyc: ok
    xs.append(9)
    return peek()


# record source
def record() -> int32:
    ys = Rec(5)

    def peek() -> int32:
        return ys.x
    xs = ys  # tpyc: ok
    xs.x += 4
    return peek()


class Holder:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    # method position
    def run(self) -> int32:
        ys = mk()

        def peek() -> int32:
            return len(ys) + self.n
        xs = ys  # tpyc: ok
        xs.append(9)
        return peek()


# inverse: the closure captures ANOTHER name, so the alias still moves
def still_moves() -> int32:
    other = mk()
    ys = mk()

    def peek() -> int32:
        return len(other)
    xs = ys  # tpyc: ok
    xs.append(9)
    return peek() + len(xs)


def main() -> None:
    print("container", container())
    print("record", record())
    print("method", Holder().run())
    print("still_moves", still_moves())


main()
