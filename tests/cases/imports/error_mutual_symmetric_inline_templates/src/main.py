# Genuinely symmetric completeness cycle: a's inline template
# returns B by value AND b's inline template returns A by value.
# Each .hpp would need the peer's complete layout for the inline
# template body to compile, but no header strategy can satisfy
# both directions simultaneously. v1 rejects via the function
# signature walk; v2's header-completeness SCC gate will continue
# to reject (with both-ends-named diagnostics).
from a import A, make_b_via
from b import B, make_a_via

def main() -> None:
    pass

main()
