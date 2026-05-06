# Mutual cyclic name binding where a's method calls into b, and
# b's function takes a's record type by reference. Sema accepts the
# cycle via skeletal cross-module pre-registration; the C++ build
# uses the per-module fwd.hpp emitted for each cycle member to
# break the complete-type include.
from a import A
from b import H

def main() -> None:
    a = A(7)
    print(H(a))         # b's H, takes A by reference
    print(a.twice())   # a.twice() -> H(self) * 2, exercising cross-cycle call

main()
