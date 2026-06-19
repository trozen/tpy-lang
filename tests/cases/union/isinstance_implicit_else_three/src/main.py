# Two sequential isinstance-and-return narrow a 3-member union down to the last
# member at the final fall-through.
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
    if isinstance(v, B):
        return v.bx
    return v.cx          # v is C here

def main() -> None:
    print(f(A(1)))
    print(f(B(2)))
    print(f(C(3)))

main()
