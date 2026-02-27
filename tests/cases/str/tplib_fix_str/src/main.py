# Test tplib.FixStr: fixed-capacity string imported from the standard library.
from tpy import Int32, Char, copy
from tplib import FixStr


def main() -> None:
    # -- empty, append, len --
    s = FixStr[16]()
    print(len(s))               # 0
    h: Char = "h"
    i: Char = "i"
    s.append(h)
    s.append(i)
    print(len(s))               # 2

    # -- getitem --
    print(s[0])                 # h
    print(s[1])                 # i

    # -- setitem --
    o: Char = "o"
    s[1] = o
    print(s[1])                 # o

    # -- pop --
    print(s.pop())              # o
    print(len(s))               # 1

    # -- iter --
    e: Char = "e"
    y: Char = "y"
    s.append(e)
    s.append(y)
    for c in s:
        print(c)                # h e y

    # iterate again
    for c in s:
        print(c)                # h e y

    # -- str / f-string --
    print(s)                    # hey
    greeting: str = str(s)
    print(greeting)             # hey
    print(f"val={s}")           # val=hey

    # -- copy --
    t = copy(s)
    b: Char = "b"
    t[0] = b
    print(s[0])                 # h (original unchanged)
    print(t[0])                 # b

    # -- clear --
    s.clear()
    print(len(s))               # 0

main()
