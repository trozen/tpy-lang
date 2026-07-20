# Branch-local Optional decls REUSING a name across sibling if bodies: the
# first registers the value-opt (scalar / owned-view) binding renders for the
# name, and the sibling's same-named PLAIN decl must not inherit them (a
# leaked binding classified `print(x)` as an optional deref, `(*x)` on a
# plain int -- the missed-restore regression this case guards).
def cond(n: int) -> bool:
    return n > 0


def maybe(n: int) -> int | None:
    if n > 1:
        return n
    return None


def maybe_s(n: int) -> str | None:
    if n > 1:
        return "yes"
    return None


def main() -> None:
    n = 3
    if cond(n):
        x: int | None = maybe(n)
        if x is not None:
            print(x)
    if cond(n):
        x = 5
        print(x)
    if cond(n):
        s: str | None = maybe_s(n)
        if s is not None:
            print(s)
    if cond(n):
        s = "plain"
        print(s)


main()
