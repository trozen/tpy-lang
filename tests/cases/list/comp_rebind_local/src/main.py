# A container local rebound from a comprehension (`xs = [f(x) for x in xs]`)
# takes the rvalue rebind slot at every position; self-iteration is safe.
# No __del__ on the elements: the superseded container's drop is deferred to scope end (BUGS.md#rebind-slot-drop-deferred).
import asyncio

from tpy import int32, error_return, ReturnException


class P:
    def __init__(self, v: int32) -> None:
        self.v = v


class Fail(Exception, ReturnException):
    pass


class Guard:
    def __enter__(self) -> None:
        pass

    def __exit__(self, et, ev, tb) -> None:
        pass


class C:
    n: int32
    xs: list[int32]

    def __init__(self) -> None:
        # constructor local
        xs: list[int32] = [1, 2, 3]
        xs = [x + 1 for x in xs]  # tpyc: ok
        self.n = xs[0]
        self.xs = [1, 2, 3]

    def bump(self, ys: list[int32]) -> int32:
        # method local
        xs: list[int32] = [1, 2, 3]
        xs = [x + ys[0] for x in xs]  # tpyc: ok
        return xs[0]

    def bump_field(self) -> None:
        # inverse: a field write, not a local rebind
        self.xs = [x + 1 for x in self.xs]  # tpyc: ok


def free_self() -> None:
    # free function, the comp iterates the list it rebinds
    xs: list[int32] = [1, 2, 3]
    xs = [x + 1 for x in xs]  # tpyc: ok
    print("free_self:", xs)


def other_source() -> None:
    # free function, the comp iterates another local
    ys: list[int32] = [1, 2, 3]
    xs: list[int32] = [9]
    xs = [y * 2 for y in ys]  # tpyc: ok
    print("other_source:", xs)


def filtered() -> None:
    # a filter clause over the rebound list
    xs: list[int32] = [1, 2, 3, 4, 5]
    xs = [x for x in xs if x % 2 == 1]  # tpyc: ok
    print("filtered:", xs)


def loop() -> None:
    # the rebind inside a loop reuses the same slot each iteration
    xs: list[int32] = [1, 2, 3]
    for _ in range(3):
        xs = [x + 1 for x in xs]  # tpyc: ok
    print("loop:", xs)


def if_body_after_empty() -> None:
    # rebind inside an if body after an empty-literal decl
    ys: list[int32] = [1, 2, 3]
    xs: list[int32] = []
    if len(ys) > 1:
        xs = [y + 1 for y in ys]  # tpyc: ok
    print("if_body_after_empty:", xs)


def both_arms(k: int32) -> int32:
    # a comp declared in BOTH arms of an if and read after it (sema hoists)
    if k > 0:
        ys = [i + 1 for i in range(4)]
    else:
        ys = [i + 2 for i in range(4)]  # tpyc: ok
    return ys[0]


def comp_first() -> None:
    # the FIRST binding is itself a comprehension, then rebound by another
    ys: list[int32] = [1, 2, 3]
    xs = [y for y in ys]  # tpyc: ok
    xs = [x + 1 for x in xs]  # tpyc: ok
    print("comp_first:", xs)


def dict_comp() -> None:
    # keyed over the old dict (`d.items()` as a comp source is
    # BUGS.md#comp-dict-items-ref-value, unrelated to the rebind)
    d: dict[int32, int32] = {1: 2}
    d = {k: d[k] + 1 for k in d}  # tpyc: ok
    print("dict_comp:", d)


def set_comp() -> None:
    s: set[int32] = {1, 2}
    s = {x + 1 for x in s}  # tpyc: ok
    print("set_comp:", sorted(s))


def str_elems() -> None:
    words: list[str] = ["a", "b"]
    words = [w + "!" for w in words]  # tpyc: ok
    print("str_elems:", words)


def tuple_elems() -> None:
    xs: list[tuple[int32, int32]] = [(1, 2)]
    xs = [(a + 1, b) for a, b in xs]  # tpyc: ok
    print("tuple_elems:", xs)


def record_elems() -> None:
    # a loan taken BEFORE the rebind keeps aliasing the OLD element
    xs: list[P] = [P(1), P(2)]
    p = xs[0]
    xs = [P(q.v + 1) for q in xs]  # tpyc: ok
    p.v = 9
    print("record_elems:", p.v, xs[0].v, xs[1].v)


def closure() -> None:
    # the enclosing body rebinds a local a nested def reads
    xs: list[int32] = [1, 2, 3]

    def inner() -> int32:
        return xs[0]

    xs = [x + 1 for x in xs]  # tpyc: ok
    print("closure:", inner())


def with_body() -> None:
    xs: list[int32] = [1, 2, 3]
    with Guard():
        xs = [x + 1 for x in xs]  # tpyc: ok
    print("with_body:", xs)


def try_finally() -> None:
    xs: list[int32] = [1, 2, 3]
    try:
        xs = [x + 1 for x in xs]  # tpyc: ok
    finally:
        print("try_finally:", xs)


def match_arm() -> None:
    xs: list[int32] = [1, 2, 3]
    k = 1
    match k:
        case 1:
            xs = [x + 1 for x in xs]  # tpyc: ok
        case _:
            pass
    print("match_arm:", xs)


@error_return(Fail)
def err_ret() -> int32:
    # @error_return body (std::expected return)
    xs: list[int32] = [1, 2, 3]
    xs = [x + 1 for x in xs]  # tpyc: ok
    return xs[0]


async def coro() -> int32:
    # inverse: async body (resumable frame, one slot per name)
    xs: list[int32] = [1, 2, 3]
    xs = [x + 1 for x in xs]  # tpyc: ok
    return xs[0]


def call_rebinds() -> None:
    # inverse: container-returning call rebinds
    xs: list[int32] = [3, 1, 2]
    xs = sorted(xs)  # tpyc: ok
    print("call_rebinds sorted:", xs)
    xs = list(x + 1 for x in xs)  # tpyc: ok
    print("call_rebinds list:", xs)


def main() -> None:
    free_self()
    other_source()
    filtered()
    loop()
    if_body_after_empty()
    print("both_arms:", both_arms(1), both_arms(0))
    comp_first()
    dict_comp()
    set_comp()
    str_elems()
    tuple_elems()
    record_elems()
    print("method_local:", C().bump([10]))
    print("ctor_local:", C().n)
    c = C()
    c.bump_field()
    print("field:", c.xs)
    closure()
    with_body()
    try_finally()
    match_arm()
    try:
        v = err_ret()
        print("error_return:", v)
    except Fail:
        pass
    print("async:", asyncio.run(coro()))
    call_rebinds()


main()

# inverse: module level (no rebind slot, a global slot instead)
gxs: list[int32] = [1, 2, 3]
gxs = [x + 1 for x in gxs]  # tpyc: ok
print("module_level:", gxs)
