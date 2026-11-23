# A compound statement's head keeps its fresh arguments and receivers until the
# whole statement ends; what it binds past the statement lives in the enclosing scope.
from tpy import int32
from part_head_compounds import Mgr, seed
import part_heads
import part_head_views
import part_head_receivers
import part_head_convs
import part_head_roots
import part_head_async
import part_head_enclosed
import part_head_placement
import part_head_compounds


class Src:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n, n + 1]


def walk(s: Src) -> list[int32]:
    return s.xs


def core() -> None:
    # the core shape: the loop iterates storage of an argument the head keeps
    for x in walk(Src(1)):  # tpyc: ok
        print("core", x)


def main() -> None:
    core()
    part_heads.main()
    part_head_views.main()
    part_head_receivers.main()
    part_head_convs.main()
    part_head_roots.main()
    part_head_async.main()
    part_head_enclosed.main()
    part_head_placement.main()
    part_head_compounds.main()


main()

# module level: a `with` inside an `if`, its target -- a global -- read after:
# the manager is kept by the module
gm = Mgr(seed(), 0)
if len(gm.name) > 0:
    with Mgr(gm.name, 5) as gm:  # tpyc: ok
        print("module in", gm.n)
junk = ["z" * 50 for _ in range(30)]
print("module after", gm.n, gm.name == seed(), len(junk))
