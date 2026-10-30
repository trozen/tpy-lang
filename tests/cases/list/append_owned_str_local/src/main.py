# An OWNED-str storage source (a concat result) appended into a `list[str]`
# moves in at its last use. The callee mutates the caller's list, so the alias
# is observable.
def add_joined(xs: list[str], a: str, b: str) -> None:
    t = a + b
    xs.append(t)  # the owned-str local at an Own[str] element slot


def main() -> None:
    xs: list[str] = ["seed"]
    add_joined(xs, "ab", "cd")
    print(len(xs), xs[0], xs[1])


main()
