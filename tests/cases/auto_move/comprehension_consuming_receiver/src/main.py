# A consuming method on a comprehension variable moves the element only when its
# clause owns it (innermost, plain target, the receiver its sole read); else it copies, warned.
import asyncio
from typing import Iterable, Iterator, Self
from tpy import Fn, Own, int32, nocopy


class Box:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]

    def finish(self: Own[Self]) -> Own[Self]:
        # Takes the receiver's storage: a source moved into it is left empty.
        kept = self
        return Box(len(kept.xs) + 1)


@nocopy
class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def finish(self: Own[Self]) -> Own[Self]:
        return self


def take(b: Own[Box]) -> Own[Box]:
    return b


def tag(b: Own[Box], n: int32) -> Own[Box]:
    b.xs.append(n)
    return b


def call(f: Fn[[], int32]) -> int32:
    return f()


def first(b: Box) -> int32:
    return b.xs[0]


def lens(ws: list[Box]) -> Own[list[int32]]:
    return [len(w.xs) for w in ws]


def heads(ws: list[Box]) -> Own[list[int32]]:
    return [w.xs[0] for w in ws]


def dict_heads(d: dict[int32, Box]) -> Own[list[int32]]:
    return [d[k].xs[0] for k in d]


# free function: a borrowed list parameter
def borrowed(ws: list[Box]) -> Own[list[Box]]:
    ws.append(Box(5))
    return [b.finish() for b in ws]  # tpyc: warning(/copies Box into owned storage; use copy\(\) to make this explicit$/)


# set comprehension
def set_comp(ws: list[Box]) -> Own[set[int32]]:
    ws.append(Box(5))
    return {first(b.finish()) for b in ws}  # tpyc: warning(/copies Box into owned storage/)


# dict comprehension over an `enumerate` tuple-unpack target
def dict_enum(ws: list[Box]) -> Own[dict[int32, Box]]:
    ws.append(Box(5))
    return {i: b.finish() for i, b in enumerate(ws)}  # tpyc: warning(/copies Box into owned storage/)


# nested clauses: the inner clause iterates a borrowed list
def nested_clauses(wss: list[list[Box]]) -> Own[list[Box]]:
    wss[0].append(Box(5))
    return [b.finish() for ws in wss for b in ws]  # tpyc: warning(/copies Box into owned storage/)


# an owned OUTER clause: its variable is read once per inner iteration
def owned_outer(src: Iterable[Own[Box]]) -> Own[list[Box]]:
    return [b.finish() for b in src for _ in range(2)]  # tpyc: warning(/copies Box into owned storage/)


# a nested comprehension in the element reads the owned variable
def nested_elem(src: Iterable[Own[Box]]) -> Own[list[list[Box]]]:
    return [[b.finish() for _ in range(2)] for b in src]  # tpyc: warning(/copies Box into owned storage/)


# owned innermost clause, the receiver its only read: the element moves
def owned_inner(src: Iterable[Own[Box]]) -> Own[list[Box]]:
    return [b.finish() for b in src]  # tpyc: ok


# owned innermost clause with a filter reading the variable: still a move
def owned_filter(src: Iterable[Own[Cell]]) -> Own[list[Cell]]:
    return [c.finish() for c in src if c.n > 0]  # tpyc: ok


# owned dict comprehension whose key also reads the variable
def owned_dict_key(src: Iterable[Own[Box]]) -> Own[dict[int32, Box]]:
    return {len(b.xs): b.finish() for b in src}  # tpyc: warning(/copies Box into owned storage/)


# the key reads the variable inside a nested comprehension
def nested_key(src: Iterable[Own[Box]]) -> Own[dict[int32, Box]]:
    return {sum([len(b.xs) for _ in range(1)]): b.finish() for b in src}  # tpyc: warning(/copies Box into owned storage/)


# two consuming receivers on one element
def double(src: Iterable[Own[Box]]) -> Own[list[tuple[Box, Box]]]:
    return [(b.finish(), b.finish()) for b in src]  # tpyc: warning(/copies Box/) warning(/copies Box/)


# a local of the function body shares the variable's name
def shadow_local(ws: list[Box]) -> None:
    ws.append(Box(5))
    b = Box(9)
    rs = [b.finish() for b in ws]  # tpyc: warning(/copies Box into owned storage; use copy\(\) to make this explicit$/)
    rs[0].xs.append(7)
    print("shadow_local", len(b.xs), heads(rs), lens(ws))


# the enclosing local was consumed before the comprehension rebinds its name
def consumed_outer(ws: list[Box]) -> Own[list[Box]]:
    ws.append(Box(5))
    b = Box(9)
    # Copies too: the comprehension's `b` counts as a later read (BUGS.md#comp-var-read-extends-enclosing-local).
    old = b.finish()  # tpyc: warning(/copies Box into owned storage/)
    rs = [b.finish() for b in ws]  # tpyc: warning(/copies Box into owned storage/)
    rs.append(old)
    return rs


# a `for` variable of the function body shares the variable's name
def shadow_for(ws: list[Box]) -> None:
    ws.append(Box(5))
    for b in [Box(9)]:
        rs = [b.finish() for b in ws]  # tpyc: warning(/copies Box into owned storage/)
        print("shadow_for", len(b.xs), heads(rs), lens(ws))


# an owned variable passed to an `Own` parameter moves; the warning is a false positive (BUGS.md#comp-var-own-arg-false-copy-warning)
def owned_arg(src: Iterable[Own[Box]]) -> Own[list[Box]]:
    return [take(b) for b in src]  # tpyc: warning(/copies Box into owned storage/)


def fresh() -> Own[list[Box]]:
    return [Box(7)]


# an enclosing `for` variable sharing the name: the comprehension's `take(b)` does not make it a consuming loop
def loop_owned_arg() -> None:
    ws = [Box(1), Box(2)]
    for b in ws:
        rs = [take(b) for b in fresh()]  # tpyc: warning(/copies Box into owned storage/)
        rs[0].xs.append(8)
        print("loop_owned_arg", len(b.xs), heads(rs), lens(rs))


# a nested comprehension in the element, inside a `for` statement
def in_loop(src: Iterable[Own[Box]]) -> None:
    for i in range(1):
        rows = [[b.finish() for _ in range(2)] for b in src]  # tpyc: warning(/copies Box into owned storage/)
        row = rows[0]
        print("in_loop", i, row[0].xs[0], row[1].xs[0])


# a nested comprehension rebinding the name is not a read of the variable: the move stays
def shadowing(src: Iterable[Own[Box]], other: list[Box]) -> Own[list[Box]]:
    return [tag(b.finish(), sum([len(b.xs) for b in other])) for b in src]  # tpyc: ok


# a lambda in the element captures the variable: the receiver copies
def lambda_capture(src: Iterable[Own[Box]]) -> Own[list[Box]]:
    return [tag(b.finish(), call(lambda: len(b.xs))) for b in src]  # tpyc: warning(/copies Box into owned storage/)


# method body
class Holder:
    ws: list[Box]

    def __init__(self) -> None:
        self.ws = [Box(1)]

    def collect(self) -> Own[list[Box]]:
        self.ws.append(Box(5))
        return [b.finish() for b in self.ws]  # tpyc: warning(/copies Box into owned storage/)


# generator body
def gen(ws: list[Box]) -> Iterator[int32]:
    ws.append(Box(5))
    rs = [b.finish() for b in ws]  # tpyc: warning(/copies Box into owned storage/)
    yield rs[0].xs[0]


# async body
async def coro(ws: list[Box]) -> int32:
    ws.append(Box(5))
    rs = [b.finish() for b in ws]  # tpyc: warning(/copies Box into owned storage/)
    await asyncio.sleep(0)
    return rs[0].xs[0]


def main() -> None:
    ws = [Box(1)]
    r = borrowed(ws)
    r[0].xs.append(9)
    print("borrowed", heads(r), lens(ws))

    ws = [Box(1)]
    print("set", sorted(set_comp(ws)), lens(ws))

    ws = [Box(1)]
    d = dict_enum(ws)
    print("dict_enum", dict_heads(d), lens(ws))

    # Annotated: BUGS.md#literal-local-array-at-container-param-build.
    wss: list[list[Box]] = [[Box(1)]]
    r = nested_clauses(wss)
    inner = wss[0]
    print("nested_clauses", heads(r), lens(inner))

    r = owned_outer([Box(1), Box(2)])
    print("owned_outer", heads(r))
    rows = nested_elem([Box(1)])
    print("nested_elem", heads(rows[0]))
    r = owned_inner([Box(1), Box(2)])
    print("owned_inner", heads(r))
    # Built by appends: BUGS.md#literal-local-array-at-container-param-build.
    cs: list[Cell] = []
    cs.append(Cell(3))
    cs.append(Cell(0))
    kept = owned_filter(cs)
    print("owned_filter", len(kept), kept[0].n)
    d = owned_dict_key([Box(1)])
    print("owned_dict_key", sorted(d), dict_heads(d))
    d = nested_key([Box(1)])
    print("nested_key", sorted(d), dict_heads(d))
    pairs = double([Box(1)])
    one, two = pairs[0]
    print("double", one.xs[0], two.xs[0])

    shadow_local([Box(1)])
    ws = [Box(1)]
    r = consumed_outer(ws)
    print("consumed_outer", heads(r), lens(ws))
    shadow_for([Box(1)])
    r = owned_arg([Box(1), Box(2)])
    print("owned_arg", heads(r))
    loop_owned_arg()

    in_loop([Box(1)])
    other = [Box(3), Box(4)]
    r = shadowing([Box(1)], other)
    print("shadowing", heads(r), lens(r))
    r = lambda_capture([Box(1)])
    print("lambda_capture", heads(r), lens(r))

    h = Holder()
    r = h.collect()
    print("method", heads(r), lens(h.ws))

    ws = [Box(1)]
    for v in gen(ws):
        print("generator", v, lens(ws))

    ws = [Box(1)]
    print("async", asyncio.run(coro(ws)), lens(ws))


main()

# module level
mod_ws = [Box(1)]
mod_ws.append(Box(5))
mod_rs = [b.finish() for b in mod_ws]  # tpyc: warning(/copies Box into owned storage/)
mod_rs[0].xs.append(9)
print("module", heads(mod_rs), lens(mod_ws))
