# A property getter is a method call wearing field-access syntax, so a chained
# compare must bind it to a temp. Inlining it rendered the intermediate into
# both pairs (getter ran twice), and leaving a non-duplicable first operand
# unbound ran it after the intermediate it precedes. A plain field read has no
# hidden call and must still inline -- the snapshot pins that inverse.
calls = 0
# Evaluation order as digits, appended left to right: 1 = first operand,
# 2 = middle, 3 = last. Source order is 123; the unbound first operand gave 213.
# Annotated as a workaround: a bare `0` infers Int32, and a plain
# re-assignment does not currently widen it to the BigInt the RHS produces.
order: int = 0


def bump(tag: int, v: int) -> int:
    global calls, order
    calls += 1
    order = order * 10 + tag
    return v


class P:
    plain: int

    def __init__(self) -> None:
        self.plain = 5

    @property
    def mid(self) -> int:
        return bump(2, 5)

    @property
    def first(self) -> int:
        return bump(1, 1)


def main() -> None:
    global calls, order
    p = P()

    # An intermediate appears in two pairs; the inline arm would render it twice.
    calls = 0
    in_range = 1 < p.mid < 10
    print("in range:", in_range, "calls:", calls)

    # Still exactly once when the second pair is false.
    calls = 0
    out_of_range = 1 < p.mid < 3
    print("out of range:", out_of_range, "calls:", calls)

    # The first operand must evaluate before the intermediate it precedes.
    order = 0
    ordered = p.first < bump(2, 5) < bump(3, 10)
    print("ordered:", ordered, "order:", order)

    # A false leading pair short-circuits the trailing operand entirely.
    calls = 0
    short = 1 < 0 < p.mid
    print("short-circuit:", short, "calls:", calls)

    # Inverse: no hidden call behind a plain field, so it stays inlined.
    calls = 0
    plain_range = 1 < p.plain < 10
    print("plain field:", plain_range, "calls:", calls)


main()
