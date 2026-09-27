# A name first bound in the later arms of an if/elif chain (not in the head's
# arm) is declared by each arm at its own binding, at the one joined type.
import asyncio
from typing import Iterator, Optional
from tpy import int32


class Node:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    def show(self, k: int32) -> None:
        # method
        if k == 1:
            print("method one")
        elif k == 2:
            r = "two"  # tpyc: ok
            print("method " + r)
        else:
            r = "other"  # tpyc: ok
            print("method " + r)


# free function
def free(k: int32) -> None:
    if k == 1:
        print("free one")
    elif k == 2:
        r = "two"  # tpyc: ok
        print("free " + r)
    else:
        r = "other"  # tpyc: ok
        print("free " + r)


# `else:` over a nested `if` is the same AST as `elif`
def else_if(k: int32) -> None:
    if k == 1:
        print("else_if one")
    else:
        if k == 2:
            r = "two"  # tpyc: ok
            print("else_if " + r)
        else:
            r = "other"  # tpyc: ok
            print("else_if " + r)


# 3-link chain, bound in the last two arms only
def chain2(k: int32) -> None:
    if k == 1:
        print("chain2 one")
    elif k == 2:
        print("chain2 two")
    elif k == 3:
        r = "three"  # tpyc: ok
        print("chain2 " + r)
    else:
        r = "other"  # tpyc: ok
        print("chain2 " + r)


# 3-link chain, bound in the last three arms
def chain3(k: int32) -> None:
    if k == 1:
        print("chain3 one")
    elif k == 2:
        r = "two"  # tpyc: ok
        print("chain3 " + r)
    elif k == 3:
        r = "three"  # tpyc: ok
        print("chain3 " + r)
    else:
        r = "other"  # tpyc: ok
        print("chain3 " + r)


# nested def
def nested(k: int32) -> None:
    def inner() -> None:
        if k == 1:
            print("nested one")
        elif k == 2:
            r = "two"  # tpyc: ok
            print("nested " + r)
        else:
            r = "other"  # tpyc: ok
            print("nested " + r)
    inner()


# loop body
def loop(k: int32) -> None:
    for i in range(2):
        if k == 1:
            print("loop one")
        elif k == 2:
            r = i * 2  # tpyc: ok
            print("loop " + str(r))
        else:
            r = i * 3  # tpyc: ok
            print("loop " + str(r))


# try body
def in_try(k: int32) -> None:
    try:
        if k == 1:
            print("try one")
        elif k == 2:
            r = "two"  # tpyc: ok
            print("try " + r)
        else:
            r = "other"  # tpyc: ok
            print("try " + r)
    except ValueError:
        print("try caught")


# match arm containing the chain
def in_match(k: int32) -> None:
    match k:
        case 0:
            print("match zero")
        case _:
            if k == 1:
                print("match one")
            elif k == 2:
                r = "two"  # tpyc: ok
                print("match " + r)
            else:
                r = "other"  # tpyc: ok
                print("match " + r)


# reference type: the arm's local aliases the object, a write through it shows
def ref(k: int32, a: Node, b: Node) -> None:
    if k == 1:
        print("ref one")
    elif k == 2:
        n = a  # tpyc: ok
        n.v += 10
        print("ref " + str(a.v))
    else:
        n = b  # tpyc: ok
        n.v += 20
        print("ref " + str(b.v))


# tuple unpack binding
def unpack(k: int32) -> None:
    if k == 1:
        print("unpack one")
    elif k == 2:
        a, b = 1, 2  # tpyc: ok
        print("unpack " + str(a + b))
    else:
        a, b = 3, 4  # tpyc: ok
        print("unpack " + str(a + b))


# walrus as the arm's first binding, in a condition and in an expression
def walrus(k: int32) -> None:
    if k == 1:
        print("walrus one")
    elif k == 2:
        if (w := k * 2) > 0:  # tpyc: ok
            print("walrus " + str(w))
    else:
        print("walrus " + str(w := k * 3))  # tpyc: ok
        print("walrus " + str(w))


# sibling arms that both return, each binding the name by walrus
def walrus_return(k: int32) -> int32:
    if k == 1:
        print("walrus_return " + str(w := k * 2))  # tpyc: ok
        return w
    else:
        print("walrus_return " + str(w := k * 3))  # tpyc: ok
        return w


# Optional-narrowing head
def narrow(x: Optional[int32], k: int32) -> None:
    if x is None:
        print("narrow none")
    elif k == 2:
        r = "two"  # tpyc: ok
        print("narrow " + r + " " + str(x))
    else:
        r = "other"  # tpyc: ok
        print("narrow " + r + " " + str(x))


# disagreeing arm types: both arms declare the joined `int32 | None`
def none_int(k: int32) -> None:
    if k == 1:
        print("none_int one")
    elif k == 2:
        r = None  # tpyc: ok
        print("none_int", r)
    else:
        r = 5  # tpyc: ok type(/int32 \| None/)
        print("none_int", r)


# disagreeing arm types: a literal and a concatenation declare one `str`
def str_mix(k: int32, a: str) -> None:
    if k == 1:
        print("str_mix one")
    elif k == 2:
        r = "lit"  # tpyc: ok type(str)
        print("str_mix " + r)
    else:
        r = a + "!"  # tpyc: ok type(str)
        print("str_mix " + r)


# the head arm returns, so a read after the chain is the function's predecl
def after_return(k: int32) -> str:
    if k == 1:
        return "after_return one"
    elif k == 2:
        r = "two"  # tpyc: ok
    else:
        r = "other"  # tpyc: ok
    return "after_return " + r


# every arm binds: one predecl in front of the whole chain
def all_three(k: int32) -> None:
    if k == 1:
        r = "one"  # tpyc: ok
    elif k == 2:
        r = "two"  # tpyc: ok
    else:
        r = "other"  # tpyc: ok
    print("all_three " + r)


# a rebind after the chain declares a fresh local (owning storage for a
# literal: BUGS.md#str-rebind-after-block-local-owns)
def rebind_after(k: int32) -> None:
    if k == 1:
        print("rebind_after one")
    elif k == 2:
        r = "two"  # tpyc: ok
        print("rebind_after " + r)
    else:
        r = "other"  # tpyc: ok
        print("rebind_after " + r)
    r = "after"  # tpyc: ok
    print("rebind_after " + r)


# an arm binding twice (declare, then assign) and one binding in a nested if
def twice(k: int32) -> None:
    if k == 1:
        print("twice one")
    elif k == 2:
        r = "two"  # tpyc: ok
        r = r + "!"  # tpyc: ok
        print("twice " + r)
    else:
        if k == 3:
            r = "three"  # tpyc: ok
        else:
            r = "other"  # tpyc: ok
        print("twice " + r)


# generator body: the name is a frame field
def gen(k: int32) -> Iterator[str]:
    if k == 1:
        yield "gen one"
    elif k == 2:
        r = "two"  # tpyc: ok
        yield "gen " + r
        yield "gen again " + r
    else:
        r = "other"  # tpyc: ok
        yield "gen " + r


# a match capture as the else arm's first binding, read after the match
def capture(k: int32) -> None:
    if k == 1:
        print("capture one")
    elif k == 2:
        v = 20  # tpyc: ok
        print("capture two", v)
    else:
        match k:
            case v:  # tpyc: ok
                pass
        print("capture else", v)


# a nested def as the later arms' first binding, called in its arm
def def_arm(k: int32) -> None:
    if k == 1:
        print("def_arm one")
    elif k == 2:
        def r() -> str:  # tpyc: ok
            return "two"
        print("def_arm " + r())
    else:
        def r() -> str:  # tpyc: ok
            return "other " + str(k)
        print("def_arm " + r())


# async body: the name is a coroutine frame field
async def in_async(k: int32) -> str:
    if k == 1:
        return "async one"
    elif k == 2:
        r = "two"  # tpyc: ok
        await asyncio.sleep(0)
        return "async " + r
    else:
        r = "other"  # tpyc: ok
        return "async " + r


def main() -> None:
    h = Holder()
    a = Node(1)
    b = Node(2)
    for k in range(1, 4):
        free(k)
        else_if(k)
        h.show(k)
        nested(k)
        loop(k)
        in_try(k)
        in_match(k)
        ref(k, a, b)
        unpack(k)
        walrus(k)
        print(walrus_return(k))
        narrow(7, k)
        none_int(k)
        str_mix(k, "cat")
        print(after_return(k))
        all_three(k)
        rebind_after(k)
        twice(k)
        for s in gen(k):
            print(s)
        capture(k)
        def_arm(k)
        print(asyncio.run(in_async(k)))
    for k in range(1, 5):
        chain2(k)
        chain3(k)
    narrow(None, 2)


main()

# module level: top-level statements
m = 2
if m == 1:
    print("module one")
elif m == 2:
    mr = "two"  # tpyc: ok
    print("module " + mr)
else:
    mr = "other"  # tpyc: ok
    print("module " + mr)
