# Genuinely symmetric completeness cycle: A stores B by value AND
# B stores A by value. Even C++ cannot compile this under any
# header strategy -- the layout of A depends on B's size which
# depends on A's size. This is the residual class that the v1 gate
# rejects and the future v2 header-completeness SCC gate will
# continue to reject (with both-ends-named diagnostics once it
# lands).
from a import A
from b import B

def main() -> None:
    pass

main()
