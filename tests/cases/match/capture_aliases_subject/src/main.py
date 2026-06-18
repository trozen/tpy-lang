# A match capture of a reference-type lvalue subject aliases it (mutation
# visible), like CPython; an rvalue subject owns its capture, a scalar copies.
from tpy import Own

class Box:
    def __init__(self, v: int):
        self.v = v

def make() -> Own[Box]:
    return Box(7)

def main():
    b = Box(1)
    match b:
        case q:
            q.v = 99
    print(b.v)              # 99 -- q aliased b

    b2 = Box(2)
    match b2:
        case Box() as r:    # as-pattern aliases too
            r.v = 50
    print(b2.v)             # 50

    match make():           # rvalue subject -> capture owns (no dangle)
        case s:
            s.v = 11
            print(s.v)      # 11

    n = 5
    match n:
        case m:             # scalar capture copies by value
            print(m)        # 5

main()
