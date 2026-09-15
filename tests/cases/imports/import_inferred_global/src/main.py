# An UNANNOTATED module-level binding is exported like an annotated one:
# `from consts import WIDTH`, `consts.WIDTH` and `from consts import *` all
# resolve it, and a borrow return of one is accepted in its own module.
# Sections cover each position the imported global is read from, plus the
# inverse: a function-local write to a module global's name stays local.
import consts
from typing import Iterator
from tpy import int32
from consts import WIDTH, label, sizes, shared, bump, get_shared, step
from starmod import *

# module level: an inferred global read in a top-level statement
half = WIDTH // 2  # tpyc: ok

# shadow fixtures: one inferred, one annotated, both of this module
tag = "global"
count: int32 = 1


def free_fn() -> int32:
    # free function: bare read of an imported inferred global
    return WIDTH + step  # tpyc: ok


class Sizer:
    factor: int32

    def __init__(self, factor: int32):
        self.factor = factor

    def scaled(self) -> int32:
        # method
        return WIDTH * self.factor  # tpyc: ok


def comp_total() -> int32:
    # comprehension: an inferred list global as the iterable
    doubled = [s * 2 for s in sizes]  # tpyc: ok
    total = 0
    for d in doubled:
        total += d
    return total


def gen_sizes() -> Iterator[int32]:
    # generator: yields from an inferred list global
    for s in sizes:  # tpyc: ok
        yield s + WIDTH


def closure_sum() -> int32:
    # closure: a nested def reads the imported global
    def inner(extra: int32) -> int32:
        return WIDTH + extra

    return inner(step)


def block_shadow() -> int32:
    # a name bound in an ENCLOSING BLOCK scope of this function, then
    # tuple-unpacked in an inner one: the unpack writes THAT local. The
    # fresh-local rule covers a name with no local binding at all, and the
    # scalar write and the unpack arm must answer it with one predicate --
    # a per-scope test makes the unpack declare a shadow and return 100.
    half = 100
    for _ in range(1):
        half, extra = (half + 1, 2)  # tpyc: ok
        print("inner", half, extra)
    return half


def shadows() -> None:
    # local shadow: a bare write in a function binds a fresh LOCAL of its own
    # type, never the module global -- inferred and annotated globals alike
    tag = 7  # tpyc: ok
    count = "seven"  # tpyc: ok
    print("shadow", tag, count)


def main() -> None:
    print("module", half)
    print("free", free_fn())
    print("method", Sizer(2).scaled())
    # qualified: `consts.X` on an inferred global
    print("qualified", consts.WIDTH, consts.label, len(consts.sizes))
    print("comprehension", comp_total())
    gtotal = 0
    for g in gen_sizes():
        gtotal += g
    print("generator", gtotal)
    print("closure", closure_sum())
    print("scalars", label, len(sizes), step)
    # the imported list ALIASES the module slot: an append through the
    # `from`-imported name is visible through the qualified one (a copy at
    # either import would keep one of the two at 3)
    sizes.append(4)
    print("alias", len(sizes), len(consts.sizes))
    # bound to a local first: a print argument that itself prints interleaves
    # differently under TPy (BUGS.md#print-arg-output-interleaves)
    blocked = block_shadow()
    print("block", blocked)
    # star import: the same inferred-binding export, reached via `import *`
    print("star", depth)  # tpyc: ok
    # borrow return: mutate through the returned reference and observe it on
    # the global itself (a copy would print 1)
    borrowed = get_shared()
    borrowed.n = 9
    print("borrow", shared.n)
    bump()
    print("rebind", shared.n, consts.tally)
    shadows()
    print("globals", tag, count)


main()
