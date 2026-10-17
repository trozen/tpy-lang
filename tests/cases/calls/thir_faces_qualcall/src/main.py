# Module-qualified calls at two sinks no other case reaches: a borrowed list
# result as a for-head iterable, and a Span view result landing in its decl.
import mymod


def container_iterable() -> None:
    h = mymod.Holder()
    # The for-head binds the module function's borrowed list, not a copy:
    # an element written through the holder mid-loop is what the next
    # iteration reads.
    for w in mymod.get_xs(h):  # tpyc: ok
        h.xs[1] = 80
        print("container_iterable:", w)


def record_storage() -> None:
    xs = [1, 2, 3]
    # The Span view result lands bare in its decl; it views `xs` in place.
    sp = mymod.mk_span(xs)  # tpyc: ok
    sp[0] = 40
    print("record_storage:", len(sp), xs[0])


def main() -> None:
    container_iterable()
    record_storage()


main()
