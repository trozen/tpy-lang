# A value-tuple with an owned-str member at a list element slot: the element is
# COPIED, never moved, so the source tuple still reads back after the store.
def main() -> None:
    t = ("a", 1)
    xs = [t]  # a value tuple carrying an owned str into the element slot
    print(len(xs), xs[0][0], xs[0][1], t[0])


main()
