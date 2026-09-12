# Test tplib.FixStr: fixed-capacity string imported from the standard library.
from tpy import int32, char, copy
from tplib import FixStr


def main() -> None:
    # -- empty, append, len --
    s = FixStr[16]()
    print(len(s))               # 0
    h: char = "h"
    i: char = "i"
    s.append(h)
    s.append(i)
    print(len(s))               # 2

    # -- getitem --
    print(s[0])                 # h
    print(s[1])                 # i

    # -- setitem --
    o: char = "o"
    s[1] = o
    print(s[1])                 # o

    # -- pop --
    print(s.pop())              # o
    print(len(s))               # 1

    # -- iter --
    e: char = "e"
    y: char = "y"
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
    b: char = "b"
    t[0] = b
    print(s[0])                 # h (original unchanged)
    print(t[0])                 # b

    # -- clear --
    s.clear()
    print(len(s))               # 0

main()
