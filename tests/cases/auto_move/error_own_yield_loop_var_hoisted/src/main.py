# A loop variable over an Own-yielding generator that is READ AFTER the loop:
# the hoisted binding has no per-element move seed, so the head rejects rather
# than moving out of a variable that outlives the loop. The loop-scoped sibling
# is pinned by tests/cases/auto_move/own_yield_loop_var_scoped.
from typing import Iterator
from tpy import int32, Own


def gen() -> Iterator[Own[int32]]:
    yield 1
    yield 2


def collect() -> None:
    out: list[int32] = []
    # Seeded before the loop: a generator head cannot prove the loop runs, so
    # without this the post-loop read would take the may-not-be-assigned
    # reject instead of the hoisted-binding one under test.
    x = 0
    for x in gen():  # tpyc: error(/foreach\.hoist_loop_var/)
        out.append(x)
    print(x)


def main() -> None:
    collect()


main()
