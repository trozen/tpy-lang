# A @dynamic protocol param on a RESUMABLE generator frame (two yields, so the
# simple-generator peephole does not apply): the frame field is a `Src&` borrow,
# so a mutation through the param between the yields is visible to the caller.
from tpy import Int32, Own, dynamic, readonly
from typing import Iterator, Protocol


@dynamic
class Src(Protocol):
    def get(self) -> Int32: ...

    def bump(self) -> None: ...


@dynamic
class RoSrc(Protocol):
    @readonly
    def get(self) -> Int32: ...


@dynamic
class Src2[T](Protocol):
    def get(self) -> T: ...

    def bump(self) -> None: ...


class Impl:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    @readonly
    def get(self) -> Int32:
        return self.n

    def bump(self) -> None:
        self.n += 1


# An INHERITANCE conformer: the call site upcasts directly, with no RefAdapter
# temp -- the other half of the adapter-vs-upcast split.
class Inh(Src):
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def get(self) -> Int32:
        return self.n

    def bump(self) -> None:
        self.n += 1


# free: a bare @dynamic protocol param on a free generator.
def free_gen(s: Src) -> Iterator[Int32]:  # tpyc: ok
    yield s.get()
    s.bump()
    yield s.get()


class Holder:
    tag: Int32

    def __init__(self, tag: Int32) -> None:
        self.tag = tag

    # method: the same param alongside the `self` receiver capture.
    def walk(self, s: Src) -> Iterator[Int32]:  # tpyc: ok
        yield s.get() + self.tag
        s.bump()
        yield s.get() + self.tag


# readonly: `readonly[Src]` captures as a `const Src&` frame field.
def ro_gen(s: readonly[RoSrc]) -> Iterator[Int32]:  # tpyc: ok
    yield s.get()
    yield s.get() * 2


# generic: a generic @dynamic protocol behaves like the monomorphic twin.
def generic_gen(s: Src2[Int32]) -> Iterator[Int32]:  # tpyc: ok
    yield s.get()
    s.bump()
    yield s.get()


# forward: an ALREADY-ERASED `Src` forwarded from one bare-protocol-param
# generator into another -- no second adapter, the borrow passes straight
# through, and the inner bump is visible to the outer frame after the yield.
def forward_gen(s: Src) -> Iterator[Int32]:  # tpyc: ok
    yield s.get()
    for n in free_gen(s):
        yield n
    yield s.get()


# global: a module-global argument outlives every frame that borrows it.
GLOBAL_SRC = Impl(80)


# own: `Own[Src]` was already admitted -- regression guard for the adjacent arm.
# It warns because a generator cannot consume an Own[@dynamic P]: a protocol is
# not a valid field or return type, so there is nowhere for it to go.
def own_gen(s: Own[Src]) -> Iterator[Int32]:  # tpyc: warning(/never consumed/)
    yield s.get()
    s.bump()
    yield s.get()


def main() -> None:
    free_src = Impl(7)
    for n in free_gen(free_src):
        print("free", n)
    print("free after", free_src.get())

    meth_src = Impl(20)
    holder = Holder(1)
    for n in holder.walk(meth_src):
        print("method", n)
    print("method after", meth_src.get())

    ro_src = Impl(5)
    for n in ro_gen(ro_src):
        print("readonly", n)

    gen_src = Impl(30)
    for n in generic_gen(gen_src):
        print("generic", n)
    print("generic after", gen_src.get())

    # inherit: an inheritance conformer and a structural one through the SAME
    # generator, both mutated and observed.
    inh_src = Inh(40)
    for n in free_gen(inh_src):
        print("inherit", n)
    print("inherit after", inh_src.get())

    struct_src = Impl(50)
    for n in free_gen(struct_src):
        print("inherit struct", n)
    print("inherit struct after", struct_src.get())

    for n in own_gen(Impl(60)):
        print("own", n)

    # rvalue: a structural conformer passed as an RVALUE -- the call site owns
    # an `Adapter<Src, Impl>` temp the frame borrows. The bump between the two
    # yields is observable in the second yielded value, not after the loop.
    for n in free_gen(Impl(99)):
        print("rvalue", n)

    fwd_src = Impl(90)
    for n in forward_gen(fwd_src):
        print("forward", n)
    print("forward after", fwd_src.get())

    for n in free_gen(GLOBAL_SRC):
        print("global", n)
    print("global after", GLOBAL_SRC.get())


main()
