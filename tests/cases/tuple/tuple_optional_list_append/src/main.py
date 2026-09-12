# list[tuple[T | None, ...]].append() of an rvalue tuple lifts
# pointer-form -> storage-form. Per-element ownership at the boundary
# requires last-use / fresh / copy() sources -- list ownership is the
# same shape as Own[tuple] params.
from tpy import int32, copy


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def main() -> None:
    pairs: list[tuple[P | None, P | None]] = []

    # Append six pair shapes covering the per-element ownership paths
    # (last-use lvalue, fresh rvalue, None, explicit copy()). Kept inline
    # because passing `pairs` through helpers borrows it as const, which
    # the storage-form subscript reads below cannot route through
    # tuple_to_pointer<tuple<P*, P*>>.
    a = P(1)
    b = P(2)
    pairs.append((a, b))                    # last-use lvalue pair
    c = P(3)
    pairs.append((c, P(4)))                 # last-use lvalue + fresh rvalue
    pairs.append((P(5), None))              # fresh rvalue + None
    pairs.append((None, None))              # both None
    keep = P(6)
    pairs.append((copy(keep), None))        # explicit copy() preserves keep
    pairs.append((copy(keep), None))
    print(len(pairs))

    # Storage-form source (subscript) into another list's append.
    pairs2: list[tuple[P | None, P | None]] = []
    pairs2.append(pairs[0])
    pairs2.append(pairs[1])
    print(len(pairs2))

    # Read back through pointer-form destructure to verify the storage
    # round-trip.
    a0, b0 = pairs[0]
    if a0 is not None:
        print(a0.x)
    if b0 is not None:
        print(b0.x)
    a1, b1 = pairs[1]
    if a1 is not None:
        print(a1.x)
    if b1 is not None:
        print(b1.x)


main()
