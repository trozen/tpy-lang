# Verifies that a synthesized record with a non-trivial method body
# (one that requires sema type-inference on field accesses) is fully
# analyzed by pass 6. Before builder-trace expansion was moved to pass
# 5.5, the method body fell through to codegen unanalyzed and failed
# with "Could not infer type" -- only the trivial framework-default
# __init__ survived via a codegen fast path.
from tpy import Int32
from _method_builder import Counter


def main() -> Int32:
    c = Counter()
    c.add(10)
    c.add(20)
    c.add(30)
    res = c.build()
    print(res.total())
    return 0


main()
