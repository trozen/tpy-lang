# Reassigning a plain (non-Optional) record FIELD from a record rvalue:
# a ctor `self.v = Inner(n)` and a by-value call `self.v = mk(n)`. Both are
# owned rvalues copied/moved bare into the field (THIR's F1-record field-write
# rung). Mutating the stored record after each write and observing the change
# forces the value-vs-reference distinction (a silent wrong-store would show).
from tpy import Own

class Inner:
    x: int
    def __init__(self, x: int):
        self.x = x

def mk(n: int) -> Own[Inner]:
    return Inner(n)

class Holder:
    v: Inner
    def __init__(self, first: Own[Inner]):
        self.v = first
    def set_ctor(self, n: int):
        self.v = Inner(n)
    def set_call(self, n: int):
        self.v = mk(n)
    def bump(self):
        self.v.x += 1

def main():
    h = Holder(Inner(10))
    print(h.v.x)
    h.set_ctor(20)
    h.bump()
    print(h.v.x)
    h.set_call(30)
    h.bump()
    print(h.v.x)

main()
