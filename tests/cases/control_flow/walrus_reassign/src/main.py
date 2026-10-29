# Walrus reassignment of an EXISTING value-typed local assigns in place (no
# redeclaration) and keeps the existing type. Covers scalar, optional-of-value,
# str/bytes view->owned promotion, and an enclosing-scope rebind in a comprehension.
from tpy import int32


# A value walrus rebound by every test of a `while` condition takes the
# binding record of its declaration: read after the loop, its last use
# moves it out.
def while_rebind_int(xs: list[int32]) -> int32:
    i = 0
    total = 0
    while (c := xs[i] + 1) < 3:
        i += 1
    total = c  # the walrus target's last use: moved
    return total


def while_rebind_str(xs: list[str]) -> str:
    i = 0
    kept = ""
    while len(w := xs[i] + "!") < 3:
        i += 1
    kept = w  # the walrus target's last use: moved
    return kept


def main() -> None:
    n = 5
    while (n := n - 1) > 0:
        print(n)

    x: int32 | None = 5
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
    m: int32 = 10
    xs = [m for _ in range(3) if (m := m - 1) > 0]
    print(m, len(xs))

    print("while_rebind", while_rebind_int([1, 5]),
          while_rebind_str(["a", "bb"]))

main()
