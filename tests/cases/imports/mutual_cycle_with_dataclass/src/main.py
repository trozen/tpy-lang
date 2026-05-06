# Cycle members can carry class macros (@dataclass) without breaking
# the order-independent body sema invariant. The pre-populate pass
# mints record / function skeletons before any module's macros run;
# macros then run during register_record per-module-in-topo-order
# inside the SCC, but they only emit AST that resolves through the
# already-populated skeletons.
from a import Pair, add_pair
from b import sum_pair, make_pair_sum

def main() -> None:
    print(sum_pair(Pair(3, 4)))
    print(add_pair(Pair(10, 20)))
    print(make_pair_sum(100, 200))

main()
