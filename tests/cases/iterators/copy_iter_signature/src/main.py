# `copy_iter(xs)` is an ordinary call declared `-> Iterator[Own[T]]`: it fits
# owning slots (list(), extend, sorted, an `Iterable[Own[T]]` parameter, a for
# head, a comprehension), each step a copy, so writes never reach the source.
from tpy import int32, copy_iter
from fixtures import Cell, take, bump_all
import part_bind_int_rec
import part_bind_values
import part_frames
import part_handover
import part_named
import part_shapes
import part_sources
import part_steps


def main() -> None:
    cells = [Cell(1), Cell(2)]
    # list() / extend / sorted over copy_iter: copies, the source untouched
    ys = list(copy_iter(cells))  # tpyc: ok
    ys[0].v = 9
    zs: list[Cell] = []
    zs.extend(copy_iter(cells))  # tpyc: ok
    zs[1].v = 8
    print("ctor", cells[0].v, cells[1].v, ys[0].v, zs[1].v)
    # an owning parameter takes the copies over
    got = take(copy_iter(cells))  # tpyc: ok
    got[0].v = 7
    print("owning param", bump_all(copy_iter(cells)), cells[0].v, got[0].v)
    # for head: the loop variable is a copy
    for c in copy_iter(cells):  # tpyc: ok
        c.v += 10
    print("for head", cells[0].v, cells[1].v)
    # comprehensions: each element a copy; mutating the source after the
    # comprehension leaves the copies as they were
    lc = [c for c in copy_iter(cells)]  # tpyc: ok
    sc = {c.v for c in copy_iter(cells)}  # tpyc: ok
    cells[0].v = 50
    print("comp", lc[0].v, cells[0].v, sorted(sc))
    # the copy is lazy: a source element written before its step is reached
    # is copied as written
    seen: list[int32] = []
    for c in copy_iter(cells):  # tpyc: ok
        seen.append(c.v)
        cells[1].v = 60
    print("lazy", seen)
    # a named adapter: one pass hands its copies over, the second is empty
    it = copy_iter(cells)
    print("named", len(take(it)), len(take(it)), cells[0].v)  # tpyc: ok
    # a for loop over a named adapter
    total = 0
    for c in it:  # tpyc: ok
        total += c.v
    ci = copy_iter(cells)
    for c in ci:  # tpyc: ok
        total += c.v
    print("named loop", total)
    part_frames.main()
    part_shapes.main()
    part_named.main()
    part_bind_values.main()
    part_bind_int_rec.main()
    part_steps.main()
    part_sources.main()
    part_handover.main()


main()
