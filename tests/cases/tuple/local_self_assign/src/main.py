# A pure self-assign (x = x) of a plain (non-generator) local is a no-op: it
# lowers verbatim to `x = x;` (Clang's -Wself-assign is suppressed for generated
# code) and the binding keeps its value. Scalar value-local + list pointer-local.
def main() -> None:
    x = 7
    x = x
    print(x)

    xs = [1, 2, 3]
    xs = xs
    xs[0] = 9
    print(xs[0] + xs[1] + xs[2])


main()
