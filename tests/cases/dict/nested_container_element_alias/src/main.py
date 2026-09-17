# Nested-container element reads (`g["a"]` off dict[str, list[int32]]) in
# value positions, and the same read one receiver level deeper
# (`cube[i][j]`), whose receiver is itself a container-element lvalue.
# Reference semantics are the point: the element read is a
# borrow into the dict's own storage, so a local bound from it and a callee
# that mutates its param must both be visible through the dict afterwards.
# The dicts here are built by ASSIGNMENT so the alias is what the section
# tests; the nested-literal build of the same shape is
# `dict/dict_annotated_nested_literal`.
from typing import Iterator
from tpy import int32, readonly
import asyncio


def push(xs: list[int32], v: int32) -> None:
    xs.append(v)


def read_elements(g: dict[str, list[int32]], m: list[list[int32]]) -> int32:
    # The value-position element reads themselves -- kept in their own body so
    # this half actually ROUTES through the element gate arm (main() below
    # carries positions that still reject, which would fall the whole body
    # back and leave the arm unexercised at exec).
    print(len(g["a"]))
    print(g["a"])
    row = g["a"]
    row.append(99)
    g["a"].append(4)
    return len(g["a"]) + len(m[1])


# --- the receiver is itself a container element: `cube[i][j]` --------------
# Same borrow, one hop deeper. Every section writes through the nested read
# and main() reads the change back out of the root, so a silent copy at the
# inner hop would show as a stale length.


class Grid:
    cells: list[list[list[int32]]]

    def __init__(self) -> None:
        self.cells = [[[1]]]

    def fill(self, i: int32, j: int32) -> int32:
        # method rooted at `self`
        row = self.cells[i][j]  # tpyc: ok
        row.append(8)
        self.cells[i][j].append(9)  # tpyc: ok
        return len(self.cells[i][j])


class Guard:
    def __enter__(self) -> int32:
        return 7

    def __exit__(self, kind, value, tb) -> None:
        pass


def deep_sinks(cube: list[list[list[int32]]], i: int32, j: int32) -> int32:
    # free function -- one line per value-position sink
    print("deep len", len(cube[i][j]))  # tpyc: ok
    print("deep cmp", len(cube[i][j]) > 0)  # tpyc: ok
    push(cube[i][j], 2)  # tpyc: ok
    cube[i][j].append(3)  # tpyc: ok
    row = cube[i][j]  # tpyc: ok
    row.append(4)
    cube[i][j][0] = 9  # tpyc: ok
    print("deep sub", cube[i][j][0])  # tpyc: ok
    # NOT a sink here: `for v in cube[i][j]` takes an iteration borrow the
    # tracker cannot key, so it is a located reject -- pinned by
    # iterators/error_foreach_unplaceable_chain_src. The local `row` above
    # is the workaround, and iterating IT is the same read.
    total = 0
    for v in row:  # tpyc: ok
        total += v
    return total


def deep_dict_outer(g: dict[str, list[list[int32]]], k: str) -> int32:
    # the root is a dict whose VALUE is nested
    g[k][0].append(5)  # tpyc: ok
    return len(g[k][0])


def deep_dict_inner(m: list[dict[str, list[int32]]], k: str) -> int32:
    # the inner hop is the dict subscript
    m[0][k].append(6)  # tpyc: ok
    return len(m[0][k])


def deep_readonly(cube: readonly[list[list[list[int32]]]], i: int32) -> int32:
    # a readonly root: both hops read const, and nothing writes
    return len(cube[i][i])  # tpyc: ok


def deep_loop_root(cube: list[list[list[list[int32]]]]) -> int32:
    # the root is the LOOP VAR and the read is two hops off it
    t = 0
    for plane in cube:
        plane[0][0].append(7)  # tpyc: ok
        t += len(plane[0][0])
    return t


def deep_control(cube: list[list[list[int32]]], i: int32, k: int32) -> int32:
    # `with` body, `try`/`finally` and a `match` arm each re-enter expression
    # lowering on their own
    with Guard() as g:
        cube[i][i].append(g)  # tpyc: ok
    try:
        cube[i][i].append(1)  # tpyc: ok
    finally:
        print("deep finally", len(cube[i][i]))  # tpyc: ok
    match k:
        case 0:
            return len(cube[i][i])  # tpyc: ok
        case _:
            return 0


def deep_gen(cube: list[list[list[int32]]], i: int32) -> Iterator[int32]:
    # generator -- the alias is a frame field, so the write after the
    # suspension still lands in the caller's cube
    row = cube[i][i]  # tpyc: ok
    yield len(row)
    row.append(11)
    # iterating the nested element itself is a located reject here too, so
    # the loop runs over the alias (see deep_sinks)
    for v in row:  # tpyc: ok
        yield v


async def deep_async(cube: list[list[list[int32]]], i: int32) -> int32:
    # async twin of `deep_gen`
    row = cube[i][i]  # tpyc: ok
    await asyncio.sleep(0)
    row.append(12)
    return len(cube[i][i])  # tpyc: ok


# module level: the root is a global pointer slot, so the nested read has to
# reach through it. Only the write face is here -- binding a local to a
# global's element is a located reject at module level for any depth,
# BUGS.md#global-alias-of-global-element-rejected.
MODULE_CUBE: list[list[list[int32]]] = [[[1]]]
MODULE_CUBE[0][0].append(21)  # tpyc: ok
MODULE_CUBE[0][0][0] = 20  # tpyc: ok


def main() -> None:
    print("module cube", MODULE_CUBE[0][0])
    a: list[int32] = [1, 2]
    b: list[int32] = [3]
    g: dict[str, list[int32]] = {}
    g["a"] = a
    g["b"] = b

    # A bound local aliases the dict's storage -- the mutation is observable
    # through the dict, not just through the local.
    r0: list[int32] = [1]
    r1: list[int32] = [2, 3]
    m: list[list[int32]] = [r0, r1]
    print(read_elements(g, m))

    # Same through a callee taking the element directly.
    push(g["b"], 7)
    print(g["b"])

    # Read positions that never had a per-sink row of their own.
    total = 0
    for v in g["a"]:
        total += v
    print(total)

    m[1].append(4)
    print(len(m[1]))
    print(m[1])

    # --- the doubly nested receiver ---
    # Each result is bound before the print: an argument that itself writes to
    # stdout interleaves ahead of the earlier arguments
    # (BUGS.md#print-arg-output-interleaves).
    cube: list[list[list[int32]]] = [[[1]]]
    n_free = deep_sinks(cube, 0, 0)
    print("deep free", n_free, cube[0][0])

    grid = Grid()
    n_method = grid.fill(0, 0)
    print("deep method", n_method, grid.cells[0][0])

    dg: dict[str, list[list[int32]]] = {}
    dg["a"] = [[1]]
    n_outer = deep_dict_outer(dg, "a")
    print("deep dict outer", n_outer, dg["a"][0])

    di: list[dict[str, list[int32]]] = []
    d0: dict[str, list[int32]] = {}
    d0["a"] = [1]
    di.append(d0)
    n_inner = deep_dict_inner(di, "a")
    print("deep dict inner", n_inner, di[0]["a"])

    print("deep readonly", deep_readonly(cube, 0))

    hyper: list[list[list[list[int32]]]] = [[[[1]]]]
    n_loop = deep_loop_root(hyper)
    print("deep loop root", n_loop, hyper[0][0][0])

    ctl: list[list[list[int32]]] = [[[1]]]
    n_ctl = deep_control(ctl, 0, 0)
    print("deep control", n_ctl, ctl[0][0])

    gc: list[list[list[int32]]] = [[[1]]]
    for v in deep_gen(gc, 0):
        print("deep gen", v)
    print("deep gen after", gc[0][0])

    ac: list[list[list[int32]]] = [[[1]]]
    n_async = asyncio.run(deep_async(ac, 0))
    print("deep async", n_async, ac[0][0])


main()
