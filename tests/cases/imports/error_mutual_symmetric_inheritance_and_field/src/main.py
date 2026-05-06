# Genuinely symmetric completeness cycle mixing inheritance and a
# by-value field: a inherits from B (a.hpp needs B's complete
# layout), AND b stores A by value (b.hpp needs A's complete
# layout). Neither direction can use the peer's fwd-decl. v1
# rejects via the inheritance branch (alphabetical first hit);
# v2's header-completeness SCC gate will continue to reject and
# should additionally name b.B.payload as the other end of the
# offending edge.
from a import A
from b import B

def main() -> None:
    pass

main()
