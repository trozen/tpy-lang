# Inverse of the exhaustive-elif fix: a 3-member union where if/elif rule out
# two members and the remaining member is accessed in the reachable fall-through.
# That tail extraction must SURVIVE -- the static-true guard must not over-trigger
# (the elif condition here is a real runtime check, not statically folded).
class A:
    def __init__(self, x: int):
        self.x = x
class B:
    def __init__(self, x: int):
        self.x = x
class C:
    def __init__(self, x: int):
        self.x = x

def pick(v: A | B | C) -> int:
    if isinstance(v, A):
        return v.x + 100
    elif isinstance(v, B):
        return v.x + 200
    return v.x + 300        # v is C; reachable fall-through, needs the extraction

def main() -> None:
    print(pick(A(1)))
    print(pick(B(2)))
    print(pick(C(3)))

main()
