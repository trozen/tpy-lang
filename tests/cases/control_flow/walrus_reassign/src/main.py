# Walrus reassignment of an EXISTING value-typed local assigns in place (no
# redeclaration) and keeps the existing type. Covers scalar, optional-of-value,
# str/bytes view->owned promotion, and an enclosing-scope rebind in a comprehension.
from tpy import Int32

def main() -> None:
    n = 5
    while (n := n - 1) > 0:
        print(n)

    x: Int32 | None = 5
    if (x := None) is None:
        print("none")

    s = "asd"
    if (s := s + "!"):  # owned source: must promote s to owned, else it dangles
        print(s)
    print(s)

    b = b"hi"
    if (b := b + b"!"):  # bytes view, same owned-source promotion as str
        print(len(b))

    # Comprehension walrus rebinds the enclosing-scope local; the rebind must
    # still assign in place across the comprehension's scope handling.
    m: Int32 = 10
    xs = [m for _ in range(3) if (m := m - 1) > 0]
    print(m, len(xs))

main()
