# Walrus reassignment of an existing NON-VALUE local is rejected: its storage
# form (T* / std::optional<T>) can't be reassigned in place via the walrus
# binding path without the var-decl rebind machinery. Rejected instead of
# miscompiling; full support is tracked in BUGS.md.
class Foo:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x

def main() -> None:
    f = Foo(1)
    if (f := Foo(2)).x > 0:  # tpyc: error(/walrus reassignment of non-value local 'f' is not supported yet/)
        print(f.x)

main()
