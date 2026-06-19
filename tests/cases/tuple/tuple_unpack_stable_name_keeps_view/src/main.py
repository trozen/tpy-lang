# Inverse: a reassigned target whose source is a STABLE tuple local (bound
# by-ref, elements alias the live local) stays a zero-copy view, not over-owned.
def main() -> None:
    t = ("hello world long enough", "another long string here ok")
    x = "init"
    y = "init"
    x, y = t
    print(x)
    print(y)


main()
