# A @dynamic protocol whose method takes a generic recursive alias instance
# (Tree[int]), dispatched at runtime through the Adapter/RefAdapter path.
# Guards that the dynamic-protocol codegen (base-trait + adapter overrides,
# which pass the param relative to the wrapper struct) composes with the
# recursive-alias wrapper. The alias is imported (cross-module): the same-module
# form currently fails to build -- a protocol method typed with a same-module
# recursive alias is emitted before the wrapper struct (see BUGS.md). Read-only
# traversal is intentional.
from typing import Protocol
from tpy import dynamic, Int32
from treelib import Tree, leaf_count


@dynamic
class Counter(Protocol):
    def count(self, t: Tree[int]) -> Int32: ...


class LeafCounter:
    def count(self, t: Tree[int]) -> Int32:
        return leaf_count(t)


def run(c: Counter) -> Int32:
    t: Tree[int] = [1, [2, 3], 4]
    return c.count(t)


def main() -> None:
    print(run(LeafCounter()))


main()
