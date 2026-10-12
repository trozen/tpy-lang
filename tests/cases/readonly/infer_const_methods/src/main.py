# Auto-infer const for methods that never mutate self (no explicit @readonly needed).
# Covers: pure readers, self-delegation chains, inherited protocols, dynamic vtable guard,
# field.method() deferred inference, Optional field const propagation, parameter
# receivers (a read-only call keeps the param const; a writing call, a write
# through a field, in a free function, method, generator or async body, keeps it
# mutable; a @staticmethod called through a param instance writes nothing).
import asyncio
from tpy import int32, dynamic
from typing import Iterator, Optional, Protocol

class Counter:
    count: int32

    def __init__(self) -> None:
        self.count = 0

    def increment(self) -> None:        # mutates self -- must NOT be const
        self.count += 1

    def increment_twice(self) -> None:  # delegates to self.increment() -- must NOT be const
        self.increment()
        self.increment()

    def get(self) -> int32:             # only reads -- inferred const
        return self.count

    def is_zero(self) -> bool:          # only reads -- inferred const
        return self.count == 0


class Box:
    items: list[int32]

    def __init__(self) -> None:
        self.items = []

    def push(self, x: int32) -> None:  # mutates self -- must NOT be const
        self.items.append(x)

    def push_default(self) -> None:    # calls self.push() -- must NOT be const
        self.push(0)

    def size(self) -> int32:           # only reads -- inferred const
        return len(self.items)


# self.field.method() -- calling a non-readonly method on a field IS self-mutation.
# Deferred to Phase 2 via a receiver_idx call edge; Phase 2 marks self as
# mutated because list.sort/append have unknown (conservative) mutation status.
class SortableBox:
    items: list[int32]

    def __init__(self) -> None:
        self.items = []

    def fill(self, a: int32, b: int32) -> None:   # mutates self.items -- must NOT be const
        self.items.append(a)
        self.items.append(b)

    def sort_items(self) -> None:                  # self.field.method() -- must NOT be const
        self.items.sort()

    def get_first(self) -> int32:                  # only reads -- inferred const
        return self.items[0]


# self.field.method() -- readonly method on a field is deferred to Phase 2
# and correctly resolved as non-mutating. Both direct field access and
# for-each iteration over fields are covered.
class Inner:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v

    def get(self) -> int32:
        return self.value

    def set(self, v: int32) -> None:
        self.value = v

class Outer:
    items: list[Inner]
    extra: Inner

    def __init__(self) -> None:
        self.items = [Inner(1), Inner(2)]
        self.extra = Inner(3)

    def sum_items(self) -> int32:                  # for-each + readonly method -- inferred const
        total = 0
        for item in self.items:
            total += item.get()
        return total

    def get_extra(self) -> int32:                  # field.method() readonly -- inferred const
        return self.extra.get()

    def mutate_extra(self) -> None:                # field.method() mutating -- must NOT be const
        self.items.append(Inner(4))


# Optional[UserType] field: const inference + codegen const propagation for
# optional_to_ptr narrowing in const methods.
class WithOpt:
    child: Optional[Inner]

    def __init__(self) -> None:
        self.child = Inner(5)

    def get_child_value(self) -> int32:            # Optional field + readonly -- inferred const
        c = self.child
        if c is not None:
            return c.get()
        return 0


# @dynamic protocol with a non-dynamic parent: the concrete class that implements
# DynValued must keep value() non-const so it matches the C++ pure virtual signature.
# Without the recursive ancestor walk in _dynamic_proto_requires_nonconst, value()
# would be incorrectly inferred as const, making Valued abstract.
class HasValue(Protocol):
    def value(self) -> int32: ...

@dynamic
class DynValued(HasValue, Protocol):
    pass

class Valued(DynValued):
    _n: int32

    def __init__(self, n: int32) -> None:
        self._n = n

    def value(self) -> int32:           # must NOT be const (pure virtual override)
        return self._n


def show(v: DynValued) -> None:
    print(v.value())


# Parameter receivers: the call's receiver mutation is decided in Phase 2, as
# for self, so a param only calling inferred-const methods stays const.
class Holder:
    inner: Inner

    def __init__(self) -> None:
        self.inner = Inner(9)


def read_counter(c: Counter) -> int32:  # c.get() is inferred const -- c is const
    return c.get()


def read_holder(h: Holder) -> int32:    # h.inner.get() -- h is const
    return h.inner.get()


def bump_counter(c: Counter) -> None:   # c.increment() writes -- c stays mutable
    c.increment()


def read_valued(v: Valued) -> int32:    # value() is kept non-const -- v stays mutable
    return v.value()


def poke_holder(h: Holder, v: int32) -> None:  # h.inner.set() writes -- h stays mutable
    h.inner.set(v)


# method position: a writing call on a param, not on self -- c stays mutable
class Bumper:
    def bump(self, c: Counter) -> None:
        c.increment()


# generator position: a writing call on a param -- c stays mutable
def bump_gen(c: Counter) -> Iterator[int32]:
    c.increment()
    yield c.get()


# async position: a writing call on a param -- c stays mutable
async def bump_async(c: Counter) -> int32:
    c.increment()
    await asyncio.sleep(0)
    return c.get()


class Calc:
    base: int32

    def __init__(self) -> None:
        self.base = 0

    @staticmethod
    def add(a: int32, b: int32) -> int32:
        return a + b


def use_static(k: Calc) -> int32:      # a staticmethod through k writes nothing -- k is const
    return k.add(1, 2)


def main() -> None:
    c = Counter()
    c.increment()
    c.increment_twice()
    print(c.get())
    print(c.is_zero())

    b = Box()
    b.push(1)
    b.push_default()
    print(b.size())

    sb = SortableBox()
    sb.fill(3, 1)
    sb.sort_items()
    print(sb.get_first())

    o = Outer()
    print(o.sum_items())
    print(o.get_extra())
    o.mutate_extra()
    print(o.sum_items())

    wo = WithOpt()
    print(wo.get_child_value())

    show(Valued(7))

    pc = Counter()
    print("param:", read_counter(pc))
    bump_counter(pc)
    pc.increment()
    print("param:", read_counter(pc))
    ph = Holder()
    print("param:", read_holder(ph))
    ph.inner.value = 10
    print("param:", read_holder(ph))
    print("param:", read_valued(Valued(8)))
    poke_holder(ph, 11)  # tpyc: ok
    ph.inner.value += 1
    print("field write:", read_holder(ph))
    wc = Counter()
    Bumper().bump(wc)  # tpyc: ok
    wc.increment()
    print("method:", wc.get())
    for n in bump_gen(wc):  # tpyc: ok
        print("generator:", n)
    wc.increment()
    print("generator:", wc.get())
    print("async:", asyncio.run(bump_async(wc)))  # tpyc: ok
    wc.increment()
    print("async:", wc.get())
    k = Calc()
    print("static:", use_static(k))  # tpyc: ok
    k.base = 5
    print("static:", use_static(k), k.base)

main()
