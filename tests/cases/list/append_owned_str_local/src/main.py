# An OWNED-str storage source (a concat result) appended into a `list[str]`: the
# argument takes the copy-plus-move-temp cascade, told from a view by the
# declared/param split. The callee mutates the caller's list, so the alias is
# observable. The copy before the move is BUGS.md#own-str-slot-temp-copies-owned-source.
def add_joined(xs: list[str], a: str, b: str) -> None:
    t = a + b
    xs.append(t)  # the owned-str local at an Own[str] element slot


def main() -> None:
    xs: list[str] = ["seed"]
    add_joined(xs, "ab", "cd")
    print(len(xs), xs[0], xs[1])


main()
