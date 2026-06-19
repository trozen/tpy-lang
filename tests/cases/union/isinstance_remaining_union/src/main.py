# After ruling out one member of a 3-member union, two remain (still a union):
# the later isinstance must dispatch on the original variant, not a premature
# single-member extraction.
class A:
    def __init__(self, x: int):
        self.ax = x
class B:
    def __init__(self, x: int):
        self.bx = x
class C:
    def __init__(self, x: int):
        self.cx = x

def f(v: A | B | C) -> int:
    if isinstance(v, A):
        return v.ax
    # v is B | C here -- not a single member
    if isinstance(v, B):
        return v.bx
    return v.cx

def main() -> None:
    print(f(A(1)))
    print(f(B(2)))
    print(f(C(3)))

main()
