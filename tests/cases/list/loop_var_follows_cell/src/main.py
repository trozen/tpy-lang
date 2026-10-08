# A `for` statement does not decide a container literal's element width: the
# loop variable follows the element cell, so a later wider store widens it too.
import asyncio
from typing import Callable, Iterator
from tpy import int64


def a64() -> int64:
    return 1099511627776


def big(v: list[int64]) -> None:
    v.append(5000000000)


class Cnt:
    k: int64

    def __init__(self) -> None:
        self.k = 0


class Acc:
    total: int64

    def __init__(self) -> None:
        self.total = 0

    # method: the loop sums into a field, the list widens after it
    def run(self) -> None:
        xs = [1, 2]  # tpyc: type(list[int64])
        for x in xs:  # tpyc: type(int64)
            self.total += x
        xs.append(a64())  # tpyc: ok
        print("method", self.total, xs)


# function: a list loop, then a wider append
def list_loop() -> None:
    xs = [1, 2]  # tpyc: type(list[int64])
    t = 0  # tpyc: type(int64)
    for x in xs:  # tpyc: type(int64)
        t += x
    xs.append(a64())  # tpyc: ok
    print("list", t, xs)


# a set, a dict, and the dict's three views
def views() -> None:
    s = {1}  # tpyc: type(set[int64])
    for v in s:
        print("set", v)
    s.add(a64())  # tpyc: ok
    d = {1: 10}  # tpyc: type(dict[int64, int64])
    for k in d:
        print("dict", k)
    for k in d.keys():
        print("keys", k)
    for v in d.values():
        print("values", v)
    for k, v in d.items():  # tpyc: type(int64)
        print("items", k, v)
    d[a64()] = a64()  # tpyc: ok
    print("views", len(s), a64() in s, d)
    # a membership test in a view of an undecided dict
    e = {"x": 10, "y": 20}  # tpyc: type(dict[str, int32])
    print("in-views", 20 in e.values(), "x" in e.keys())  # tpyc: ok


# a list of tuples and nested lists: each numeric leaf follows its own cell
def nested() -> None:
    ps = [(1, "a"), (2, "b")]  # tpyc: type(list[tuple[int64, str]])
    for n, name in ps:
        print("pairs", n, name)
    ps.append((a64(), "c"))  # tpyc: ok
    g = [[1, 2], [3]]  # tpyc: type(Array[list[int64], 2])
    for row in g:  # tpyc: type(list[int64])
        # the row aliases g's element: the append shows in g
        row.append(4)  # tpyc: ok
    s = 0  # tpyc: type(int64)
    for row in g:
        for x in row:
            s += x
    g[1].append(a64())  # tpyc: ok
    print("nested", ps, s, g)


# a mixed tuple: the record member aliases the list's, the number follows
def mixed() -> None:
    ps = [(1, Cnt())]  # tpyc: type(list[tuple[int64, Cnt]])
    for n, c in ps:
        c.k += 5
    ps.append((a64(), Cnt()))  # tpyc: ok
    for n, c in ps:
        print("mixed", n, c.k)


# generator body: a yield inside the loop, the wider store after it
def gen() -> Iterator[int64]:
    xs = [1, 2]  # tpyc: type(list[int64])
    for x in xs:
        yield x
    xs.append(a64())  # tpyc: ok
    yield xs[2]


# async body: an ordinary `for` with an await inside, the wider store after it
async def coro() -> int64:
    xs = [1, 2]  # tpyc: type(list[int64])
    t = 0  # tpyc: type(int64)
    for x in xs:
        t += x
        await asyncio.sleep(0)
    xs.append(a64())  # tpyc: ok
    return t + xs[2]


# continue, break / else, and the loop variable read after the loop
def after_loop() -> None:
    xs = [1, 2, 3]  # tpyc: type(list[int32])
    for x in xs:
        if x == 1:
            continue
        if x == 9:
            break
    else:
        print("else", x)
    print("after", x)
    # x is read after the loop, so the loop decides xs (a limitation)
    xs.append(4)  # tpyc: ok
    print("after-list", xs)


# a zero-trip loop over a prebound typed target (held to its type once ys
# settles), and a repeated target
def targets() -> None:
    ys = [1]  # tpyc: type(list[int64])
    ys.pop()
    x: int64 = 7
    for x in ys:  # tpyc: ok
        print("never", x)
    ys.append(a64())
    print("zero-trip", ys)
    zs = [1, 2]  # tpyc: type(Array[int32, 2])
    ws = [3]  # tpyc: type(list[int64])
    for y in zs:  # tpyc: type(int32)
        print("rep1", y)
    for y in ws:  # tpyc: type(int64)
        print("rep2", y)
    ws.append(a64())  # tpyc: ok
    print("repeat", zs, ws)
    # after the loops the name is a new variable, of its own type
    y = a64()  # tpyc: ok type(int64)
    print("fresh", y)
    # a later loop or unpack reusing a loop's name binds a new variable
    for z in zs:
        print("num", z)
    for z in ["a", "b"]:
        z += "!"  # tpyc: ok
        print("str", z)
    for z in [1.5, 2.5]:
        z += 1.0  # tpyc: ok
        print("float", z)
    z, w = "p", "q"
    z += "!"  # tpyc: ok
    print("reuse", z, w)


# a store into the loop variable stays its own: the list widens afterwards
def rebinding() -> None:
    xs = [1, 2]  # tpyc: type(list[int64])
    for x in xs:
        x = x + 1  # tpyc: ok
        x += 10  # tpyc: ok
        print("rebind", x)
    xs.append(a64())  # tpyc: ok
    print("rebind-list", xs)
    # an unpacking head and a plain unpack: each target has its own cell too
    ps = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
    for a, b in ps:
        a += 10  # tpyc: ok
        print("unpack", a, b)
    c, d = ps[0]
    c += 20  # tpyc: ok
    ps.append((a64(), 3))  # tpyc: ok
    print("unpack-list", c, d, ps)
    # a head rebinding an earlier local assigns that local
    n = 7  # tpyc: type(int32)
    zs = [4, 5]  # tpyc: type(Array[int32, 2])
    for n in zs:
        n += 1  # tpyc: ok
    print("hoisted", n, zs)


# a walrus, a nested def and a lambda over the loop variable (each decides)
def captures() -> None:
    ys = [1, 2]  # tpyc: type(list[int32])
    t = 0
    for y in ys:
        if (w := y * 2) > 2:  # tpyc: ok
            t += w

        def show() -> None:
            print("capture", y)
        show()
        f: Callable[[], int64] = lambda: y + 1
        print("lambda", f())
    ys.append(3)
    print("captures", t, ys)


# mutating the list inside its own loop still warns
def invalidation() -> None:
    xs = [1, 2]  # tpyc: type(list[int64])
    for x in xs:
        if x == 2:
            xs.append(a64())  # tpyc: warning(/Mutation of 'xs' while iterating over it/)
            break
        print("inv", x)
    print("inv-list", xs)


# a typed container parameter after the loop decides the elements
def context_after() -> None:
    ys = [1]  # tpyc: type(list[int64])
    for v in ys:
        print("ctx", v)
    big(ys)  # tpyc: ok
    print("ctx-list", ys)


def main() -> None:
    list_loop()
    Acc().run()
    views()
    nested()
    mixed()
    for v in gen():
        print("gen", v)
    print("async", asyncio.run(coro()))
    after_loop()
    targets()
    rebinding()
    captures()
    invalidation()
    context_after()


main()
