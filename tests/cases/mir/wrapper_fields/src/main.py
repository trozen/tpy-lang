# MIR pins for loan-free tuple, Optional and union fields and elements, one part per area. Storing
# a `P | None` into a field copies it (warned; CPython aliases), intended; `flag` is False when run.
from fixtures import Built, Empty, H, Num, P, Pair, read
import part_alias
import part_presence
import part_writes
import part_payload
import part_tuple
import part_sums
import part_elements
import part_refused


def main() -> None:
    a = H(P(1), 10)
    print("via_self", a.via_self(False))
    print("read", read(a))
    part_alias.main()
    part_presence.main()
    part_writes.main()
    part_payload.main()
    part_tuple.main()
    part_sums.main()
    part_elements.main()
    part_refused.main()
    e = Empty()
    if e.p is None:
        print("empty", 0)
    u = Built(8)
    if u.p is not None:
        print("built", u.p.x)
    pr = Pair((1, P(1)))
    print("pair", pr.t[1].x)
    m = Num(None)
    if m.k is None:
        print("num", 0)


main()
