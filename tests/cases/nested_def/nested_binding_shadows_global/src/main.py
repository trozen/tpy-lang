# A nested def's or lambda's own binding named like a global, an enclosing
# name or a frame field is its own variable; reads and captures stay outer.
import asyncio
from enum import Enum
from typing import Callable, Iterator, Literal, overload

from tpy import int32
from tpy.extern import native_global
from tplib.box import Box

from helper import VAL


class Holder:
    def __init__(self, v: int32) -> None:
        self.v = v

    def other_v(self, other: "Holder") -> int32:
        # a lambda param named `self` is the lambda's own, not the receiver
        get: Callable[[Holder], int32] = lambda self: self.v  # tpyc: ok
        return get(other)


class Ctx:
    def __enter__(self) -> int32:
        return 42

    def __exit__(self, exc_type: None, exc_val: None, exc_tb: None) -> None:
        pass


label = "global label"
cnt = 0
total = 100
pair = (1, 2)
first = "global first"
hh = Holder(5)
bx = Box(7)
items = [1, 2, 3]
i = "global i"
cm = "global cm"
err = "global err"
w = 50
# never read, so the program needs no C++ companion
frame_count: int32 = native_global("DG_FrameCount", binding="C")


def apply(f: Callable[[int32], int32], v: int32) -> int32:
    return f(v)


def fresh_local_other_type() -> None:
    def inner() -> None:
        # a float local named like the str global
        label = 1.5  # tpyc: ok
        print("fresh_local:", label)

    inner()
    print("fresh_local global:", label)


def same_type_local() -> None:
    def inner() -> None:
        # same type as the global: must not overwrite it
        cnt = 7  # tpyc: ok
        print("same_type:", cnt)

    inner()
    print("same_type global:", cnt)


def aug_local() -> None:
    def inner() -> None:
        total = 1
        # the augmented assign updates the local
        total += 5  # tpyc: ok
        print("aug:", total)

    inner()
    print("aug global:", total)


def unpack_local() -> None:
    def inner() -> None:
        # both unpack targets are fresh locals
        first, cnt = pair  # tpyc: ok
        print("unpack:", first, cnt)

    inner()
    print("unpack global:", first, cnt)


def param_vs_slot_global() -> None:
    def ident(hh: int32) -> int32:
        # a scalar param named like a class-instance global
        return hh + 1  # tpyc: ok

    def bump(hh: Holder) -> None:
        # a record param named like it: mutates the argument, not the global
        hh.v += 10  # tpyc: ok

    mine = Holder(1)
    bump(mine)
    print("param_slot:", ident(3), mine.v, hh.v)


def param_vs_box_global() -> None:
    def show(bx: Box[int32]) -> None:
        # a Box param named like a Box global prints the argument
        print("param_box:", bx)  # tpyc: ok

    show(Box(3))
    print("param_box global:", bx)


def param_vs_imported_global() -> None:
    def plus(VAL: int32) -> int32:
        # a param named like an imported global
        return VAL + 1  # tpyc: ok

    print("param_imported:", plus(3), VAL)


def native_global_branch_local(c: bool) -> int32:
    def pick() -> int32:
        # a branch-first local named like a native global
        if c:  # tpyc: ok
            frame_count = 1
        else:
            frame_count = 2
        return frame_count

    return pick()


def lambda_param_vs_slot_global() -> None:
    # a lambda param named like a class-instance global
    print("lambda_param:", apply(lambda hh: hh + 1, 3))  # tpyc: ok


def lambda_param_vs_opt_param(p: Holder | None) -> int32:
    # lambda params named like the enclosing Optional param
    k: Callable[[Holder], int32] = lambda p: p.v  # tpyc: ok
    t: Callable[[int32], int32] = lambda p: 1 if p else 0  # tpyc: ok
    if p is None:
        return k(Holder(4)) + t(0)
    return k(p) + t(3)


def block_binders() -> None:
    def loop() -> None:
        # a for var named like a global
        for i in range(2):  # tpyc: ok
            print("for:", i)

    def ctx() -> None:
        # a with-as target named like a global
        with Ctx() as cm:  # tpyc: ok
            print("with:", cm)

    def handler() -> None:
        try:
            raise ValueError("boom")
        # an except-as name named like a global
        except ValueError as err:  # tpyc: ok
            print("except:", err)

    loop()
    ctx()
    handler()
    print("binders global:", i, cm, err)


def walrus_local() -> None:
    def inner() -> int32:
        # a walrus target named like a global binds the nested def's local
        return (w := 3)  # tpyc: ok

    print("walrus:", inner(), w)


class K:
    def m(self) -> None:
        def inner() -> None:
            # a local named like a global, in a nested def inside a method
            label = 2.5  # tpyc: ok
            print("method:", label)

        def ident(hh: int32) -> int32:
            # a param named like a class-instance global, same position
            return hh + 2  # tpyc: ok

        inner()
        print("method ident:", ident(4), label)


class Tone(Enum):
    LOW = 1
    HIGH = 2

    def bump(self) -> int:
        def grow(self: list[int]) -> int:
            # an enum companion method's nested param `self` is the list
            self.append(5)  # tpyc: ok
            return len(self)

        xs = [10]
        n = grow(xs)
        # the outer `self` is still the member; xs saw the append
        return n * 100 + len(xs) * 10 + self.value


def gen(rs: list[Holder]) -> Iterator[int32]:
    step = 0

    def inner() -> None:
        # a local named like a global, in a nested def inside a generator
        label = 3.5  # tpyc: ok
        print("gen local:", label)

    def ident(hh: int32) -> int32:
        # a param named like a class-instance global, same position
        return hh + 1  # tpyc: ok

    def double(step: int32) -> int32:
        # a param named like this generator's own frame field `step`
        return step * 2  # tpyc: ok

    inner()
    step = ident(4)
    yield double(step)
    # a lambda param named like the frame's loop variable `r`
    out = sorted(rs, key=lambda r: -r.v)  # tpyc: ok
    for r in out:
        yield r.v
    print("gen after:", step)


def reads_only() -> None:
    def inner() -> None:
        # only READS globals: each is the module binding
        print("reads:", label, hh.v, cnt, len(items))  # tpyc: ok
        # mutations through a read reach the global's object
        hh.v += 1
        items.append(4)

    inner()
    print("reads after:", hh.v, len(items))


def narrowed(o: Holder | None) -> None:
    if o is not None:
        # the lambda reads the enclosing function's narrowing of `o`
        get: Callable[[], int32] = lambda: o.v  # tpyc: ok

        def own(o: int32) -> int32:
            # a nested param named like the narrowed `o` is its own binding
            return o + 1  # tpyc: ok

        print("narrowed:", get(), own(1))
    else:
        print("narrowed: none")


def gen_frame_field() -> Iterator[int32]:
    for n in range(2):
        wf = n * 100
        yield wf

    def helper() -> None:
        for j in range(2):
            # a body local named like this generator's frame field `wf`
            wf = j  # tpyc: ok
        print("frame_field helper:", wf)

    helper()
    # the frame field keeps its own value after the call
    print("frame_field after:", wf)


def loop_var_vs_enclosing_local() -> None:
    v = 5

    def inner() -> None:
        # a loop var named like an enclosing local is the nested def's own
        for v in range(2):  # tpyc: ok
            print("loop_var:", v)

    inner()
    print("loop_var outer:", v)


@overload
def classify(x: Literal[1]) -> str: ...
@overload
def classify(x: Literal[2]) -> str: ...
def classify(x: int) -> str:
    def inner(x: int) -> str:
        # a param named like the enclosing @overload implementation's param
        # is not folded against the stub's literal
        if x == 1:  # tpyc: ok
            return "inner-one"
        return "inner-other"

    if x == 1:
        return "outer-one " + inner(2)
    return "outer-two " + inner(1)


def match_capture() -> None:
    def inner(v: int32) -> None:
        match v:
            # a capture named like a global binds the nested def's local
            case cnt:  # tpyc: ok
                print("match:", cnt)

    inner(9)
    print("match global:", cnt)


def del_local() -> None:
    def inner() -> None:
        label = "inner label"
        print("del:", label)
        # deleting the nested def's own local leaves the global alone
        del label  # tpyc: ok

    inner()
    print("del global:", label)


async def async_host() -> int32:
    step = 2

    def inner() -> None:
        # a local named like a global, in a nested def inside an async def
        label = 4.5  # tpyc: ok
        print("async local:", label)

    def double(step: int32) -> int32:
        # a param named like this coroutine's own frame field `step`
        return step * 2  # tpyc: ok

    inner()
    await asyncio.sleep(0)
    return double(step + 1)


def main() -> None:
    fresh_local_other_type()
    same_type_local()
    aug_local()
    unpack_local()
    param_vs_slot_global()
    param_vs_box_global()
    param_vs_imported_global()
    print("native_local:", native_global_branch_local(True),
          native_global_branch_local(False))
    lambda_param_vs_slot_global()
    print("lambda_opt_param:", lambda_param_vs_opt_param(Holder(10)),
          lambda_param_vs_opt_param(None))
    block_binders()
    walrus_local()
    K().m()
    print("enum_method:", Tone.LOW.bump(), Tone.HIGH.bump())
    for v in gen([Holder(1), Holder(3)]):
        print("gen:", v)
    reads_only()
    narrowed(Holder(8))
    narrowed(None)
    h1 = Holder(1)
    h2 = Holder(2)
    print("lambda_self:", h1.other_v(h2))
    for v in gen_frame_field():
        print("frame_field:", v)
    loop_var_vs_enclosing_local()
    print("overload:", classify(1))
    print("overload:", classify(2))
    match_capture()
    del_local()
    r = asyncio.run(async_host())
    print("async:", r)


main()
