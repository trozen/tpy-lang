# Harness case for the EXACT-NAME form of the options.json
# `snapshot_lib_modules` key (the harness/stdlib_render case pins the glob
# form): this case commits tplib.array_list's generated C++ into its own
# expected/ tree, so a library module's emission is pinned as a real program
# instantiates it rather than only as a bare import does.
# The program itself just instantiates the generic container over a user record
# and mutates through a borrowed element, so a silent copy would show up as a
# missing mutation rather than as matching output.
from tpy import int32
from tplib import ArrayList


class Counter:
    hits: int32

    def __init__(self, hits: int32) -> None:
        self.hits = hits

    def bump(self) -> None:
        self.hits += 1


def main() -> None:
    counters = ArrayList[Counter, 4]()
    counters.append(Counter(0))
    counters.append(Counter(10))

    # Mutated in place through the container: the appended records were moved
    # in, not copied, so the bumps must be visible when read back.
    counters[0].bump()
    counters[0].bump()
    counters[1].bump()

    for c in counters:
        print(c.hits)
    print(len(counters))


main()
