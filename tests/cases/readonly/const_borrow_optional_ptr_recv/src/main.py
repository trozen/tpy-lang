# A borrow-local off a narrowed pointer-repr Optional param inferred `const H*` must
# spell `const` (REF_ALIAS + OPTIONAL_TO_PTR); a mutating function keeps mutable forms.
class A:
    v: int
    def __init__(self, v: int) -> None:
        self.v = v

class H:
    g: A
    f: A | None
    def __init__(self) -> None:
        self.g = A(1)
        self.f = A(2)

def read_ref(h: H | None) -> int:      # h inferred const H*; REF_ALIAS off narrowed h
    if h is None:
        return -1
    g = h.g
    return g.v

def read_opt(h: H | None) -> int:      # OPTIONAL_TO_PTR lift off narrowed h
    if h is None:
        return -1
    q = h.f
    if q is not None:
        return q.v
    return -1

def bump(h: H | None) -> None:         # inverse: mutates through the borrow-local -> non-const
    if h is None:
        return
    g = h.g
    g.v += 10

def main() -> None:
    h = H()
    print(read_ref(h))     # 1
    print(read_opt(h))     # 2
    bump(h)                # mutate the shared A through the mutable borrow-local
    print(read_ref(h))     # 11 -- the readonly reader aliases the shared object, not a copy

main()
