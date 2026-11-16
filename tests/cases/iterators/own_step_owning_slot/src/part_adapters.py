# Named adapters and library iterators at an owning slot: `own_iter`,
# `copy_iter` and `heapq.merge` hand their steps over with no copy warning.
from heapq import merge
from typing import Iterable
from tpy import Own, copy_iter, int32, own_iter
from fixtures import Cell, Tok, take, take_tok


class Key:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __lt__(self, other: "Key") -> bool:
        return self.v < other.v


def keep_keys(xs: Iterable[Own[Key]]) -> Own[list[Key]]:
    out: list[Key] = []
    for k in xs:
        out.append(k)
    return out


def main() -> None:
    # named adapters at the owning slot: an `own_iter` of @nocopy
    # elements moves them, a `copy_iter` hands over its copies (one pass
    # each: a second pass over an exhausted copy_iter is empty)
    toks: list[Tok] = [Tok(4), Tok(5)]
    oi = own_iter(toks)
    print("named own_iter", take_tok(oi))  # tpyc: ok
    ys: list[Cell] = [Cell(3)]
    ci = copy_iter(ys)
    print("named copy_iter", take(ci), take(ci), len(ys), ys[0].v)  # tpyc: ok
    # `heapq.merge` declares `Iterator[Own[T]]`: its steps move into an
    # owning slot with no copy warning (merge yields copies of its inputs'
    # keys by its own documented design, so only values are printed)
    ka: list[Key] = [Key(1), Key(3)]
    kb: list[Key] = [Key(2)]
    merged = keep_keys(merge(ka, kb))  # tpyc: ok
    print("merge owning", [k.v for k in merged])
