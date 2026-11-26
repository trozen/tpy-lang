# Positions that OWN what they are handed take a warned copy of a read off a
# temporary, as its method spelling does: what is copied belongs to the
# temporary alone, so no one can observe the copy. A field read off a call
# handing the read back is used within the statement and needs no copy.
from typing import Iterator
from tpy import Own, int32


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class H:
    xs: list[int32]
    r: Rec

    def __init__(self) -> None:
        self.xs = [7, 8]
        self.r = Rec(5)

    @property
    def items(self) -> list[int32]:
        return self.xs

    @property
    def rec(self) -> Rec:
        return self.r


def mk() -> Own[H]:
    return H()


class Keeper:
    ys: list[int32]

    def __init__(self) -> None:
        # field write
        self.ys = mk().items  # tpyc: warning(/copies list\[int32\] into field/)


def keep(r: Rec) -> Rec:
    return r


def give() -> Own[Rec]:
    # owning return
    return mk().rec  # tpyc: warning(/copies Rec into owned storage/)


def gen() -> Iterator[Own[Rec]]:
    # owning yield
    yield mk().rec  # tpyc: warning(/copies Rec into owned storage/)


def main() -> None:
    k = Keeper()
    k.ys.append(1)
    print("field_write:", len(k.ys))
    print("return_own:", give().n)
    for r in gen():
        print("yield_own:", r.n)
    # a field read off a bodied callee's result that hands the read back
    print("kept_field:", keep(mk().rec).n)  # tpyc: ok
