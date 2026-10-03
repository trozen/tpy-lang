# MIR pins for user record method calls through per-method summaries (receiver = parameter 0):
# bodies, callers per result position, conflicts beside safe siblings, kept refusals.
from typing import Protocol, Self
from tpy import int32, Own, StrView, dynamic, readonly


class Counter:
    n: int32
    name: str
    items: list[int32]

    # constructor: scalar, owned-leaf and empty container fields
    def __init__(self, name: str) -> None:  # tpyc: mir(covered)
        self.n = 0
        self.name = name
        self.items = []

    # method body: a scalar field write
    def bump(self) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.n += 1

    # method body: a container field grown
    def push(self, v: int32) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.items.append(v)  # tpyc: mir_write(self.items[structure])

    # method body: a container field element written
    def set_at(self, i: int32, v: int32) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.items[i] = v  # tpyc: mir_write(self.items[elements])

    # method body: an owned-leaf field replaced
    def rename(self, s: str) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.name = s  # tpyc: mir_write(self.name)

    # method body: a field iterated through a readonly receiver
    @readonly
    def total(self) -> int32:  # tpyc: mir(covered) mir_summary(known)
        t = 0
        for v in self.items:
            t += v
        return t

    # method body: a str field returned by value
    @readonly
    def label(self) -> str:  # tpyc: mir(covered) mir_summary(known)
        return self.name  # tpyc: warning(/returns a copy of str field/)

    # kept refusal: a view of a field returned -- a result rooted inside the receiver has no
    # origin a summary can name
    def tag(self) -> StrView:  # tpyc: mir(covered) mir_summary(opaque /^unsupported return origin type or access$/)
        return self.name

    # method body: a record parameter read beside the receiver
    def absorb(self, other: "Counter") -> None:  # tpyc: mir(covered) mir_summary(known)
        self.n += other.n

    # method body: an explicit @readonly method reads its record parameter as readonly too
    @readonly
    def ahead_of(self, other: "Counter") -> bool:  # tpyc: mir(covered) mir_summary(known)
        return self.n > other.n

    # method body: the receiver returned (a borrowed result rooted at parameter 0)
    def me(self) -> "Counter":  # tpyc: mir(covered) mir_summary(known)
        return self

    # method body: a record parameter returned mutable by a method that writes nothing
    def other(self, o: "Counter") -> "Counter":  # tpyc: mir(covered) mir_summary(known)
        return o

    # method body: the receiver returned by an explicit @readonly method -- a readonly result
    @readonly
    def me_ro(self) -> "Counter":  # tpyc: mir(covered) mir_summary(known)
        return self

    # method body: a view of a str parameter returned
    def first(self, s: str) -> StrView:  # tpyc: mir(covered) mir_summary(known)
        return s[0:1]

    # method caller: a method called on self
    def bump_twice(self) -> None:  # tpyc: mir(covered)
        self.bump()
        self.bump()

    # method caller: a method called on a record parameter
    def poke(self, other: "Counter") -> None:  # tpyc: mir(covered)
        other.bump()
        self.n += other.n

    # method conflict: the receiver's field grown through a method under an iterator over it;
    # sema is silent (BUGS.md#field-loan-whole-record-callee-unchecked)
    def grow_under_iter(self, go: bool) -> int32:  # tpyc: mir(conflict /replacement/)
        t = 0
        for x in self.items:
            if go:
                self.push(x)
            t += x
        return t

    # method safe sibling: the call under the iterator writes a scalar field only
    def bump_under_iter(self) -> int32:  # tpyc: mir(covered)
        t = 0
        for x in self.items:
            self.bump()
            t += x
        return t

    # aliased argument: a body assumes its borrowed parameters may alias, so iterating
    # other.items while growing self.items conflicts at the callee
    def merge_from(self, other: "Counter") -> None:  # tpyc: mir(conflict /replacement/)
        for x in other.items:
            self.items.append(x)

    # kept refusal: a recursive method has no summary
    def countdown(self, k: int32) -> int32:  # tpyc: mir_summary(opaque /^recursive or recursion-dependent call$/)
        if k <= 0:
            return self.n
        return self.countdown(k - 1)

    # method body: a container field returned to the caller
    def all_items(self) -> list[int32]:  # tpyc: mir(covered)
        return self.items


class Gauge:
    level: int32

    # constructor: a scalar field
    def __init__(self, level: int32) -> None:  # tpyc: mir(covered)
        self.level = level

    # method body: the same name as Counter.bump, a different owner and field
    def bump(self) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.level += 1

    # method body: a scalar field read
    def read(self) -> int32:  # tpyc: mir(covered) mir_summary(known)
        return self.level


# free caller: a statement call writing a scalar field
def call_bump(c: Counter) -> None:  # tpyc: mir(covered)
    c.bump()


# free caller: a statement call growing a container field
def call_push(c: Counter, v: int32) -> None:  # tpyc: mir(covered)
    c.push(v)  # tpyc: mir_write(c.items[structure])


# free caller: a statement call writing a container element
def call_set_at(c: Counter, v: int32) -> None:  # tpyc: mir(covered)
    c.set_at(0, v)  # tpyc: mir_write(c.items[elements])


# free caller: a scalar result
def call_total(c: Counter) -> int32:  # tpyc: mir(covered)
    return c.total()


# free caller: an owned-leaf result bound and read
def call_label(c: Counter) -> int:  # tpyc: mir(covered)
    s = c.label()
    return len(s)


# free caller: a call inside if
def call_in_if(c: Counter, go: bool) -> None:  # tpyc: mir(covered)
    if go:
        c.bump()


# free caller: a call inside while
def call_in_while(c: Counter, k: int32) -> None:  # tpyc: mir(covered)
    i = 0
    while i < k:
        c.push(i)  # tpyc: mir_write(c.items[structure])
        i += 1


# free caller: a call inside a for over an unrelated (owned local) list
def call_in_for(c: Counter) -> None:  # tpyc: mir(covered)
    ys: list[int32] = [5, 6]
    for y in ys:
        c.push(y)  # tpyc: mir_write(c.items[structure])


# free caller: a readonly receiver calling the readonly methods
def call_readonly(c: readonly[Counter]) -> int:  # tpyc: mir(covered)
    return c.total() + len(c.label())


# free caller: a record argument beside the receiver
def call_absorb(a: Counter, b: Counter) -> None:  # tpyc: mir(covered)
    a.absorb(b)


# free caller: a @readonly method taking a record argument
def call_ahead(a: Counter, b: Counter) -> bool:  # tpyc: mir(covered)
    return a.ahead_of(b)


# free caller: a borrowed result rooted at the receiver, bound and written through
def call_me(c: Counter) -> None:  # tpyc: mir(covered)
    r = c.me()  # tpyc: mir_borrows(r, c)
    r.n += 1


# free caller: a readonly borrowed result bound and read
def call_me_ro(c: Counter) -> int32:  # tpyc: mir(covered)
    r = c.me_ro()  # tpyc: mir_borrows(r, c)
    return r.n


# free caller: a borrowed result rooted at a record argument, bound and written through
def call_other(c: Counter, d: Counter) -> None:  # tpyc: mir(covered)
    r = c.other(d)  # tpyc: mir_borrows(r, d)
    r.n += 1


# free caller: a view result rooted at a str argument, bound and read
def call_first(c: Counter, s: str) -> int:  # tpyc: mir(covered)
    v: StrView = c.first(s)  # tpyc: mir_borrows(v, s)
    return len(v)


def take(n: int32, k: int32) -> int32:
    return n + k


# free caller: a method call as an argument of a user call
def call_as_argument(c: Counter) -> int32:  # tpyc: mir(covered)
    return take(c.total(), 1)


# free caller: the receiver and the argument read the same record
def push_own_total(c: Counter) -> None:  # tpyc: mir(covered)
    c.push(c.total())  # tpyc: mir_write(c.items[structure])


# free body: a @readonly function reads its record parameter, which it does not return
@readonly
def count_of(c: Counter) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return c.n + 1


# free caller: a @readonly free function's summary
def call_count_of(c: Counter) -> int32:  # tpyc: mir(covered)
    return count_of(c)


# free caller: two records with a same-named method -- each call takes its own owner's summary
def two_owners(c: Counter, g: Gauge) -> None:  # tpyc: mir(covered)
    c.bump()
    g.bump()


# free conflict: a container field grown through a method under an iterator over it;
# sema is silent (BUGS.md#field-loan-whole-record-callee-unchecked)
def grow_under_iter(c: Counter, go: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    t = 0
    for x in c.items:
        if go:
            c.push(x)
        t += x
    return t


# free body: a record parameter's container field grown (the write the next section reads)
def push_free(c: Counter, v: int32) -> None:  # tpyc: mir(covered) mir_summary(known)
    c.items.append(v)


# free conflict: the same growth through a free function handed the record -- reported
# through the free callee's published write; sema is silent
# (BUGS.md#field-loan-whole-record-callee-unchecked)
def free_grow_under_iter(c: Counter, go: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    t = 0
    for x in c.items:
        if go:
            push_free(c, x)
        t += x
    return t


# free safe sibling: the call under the iterator writes a scalar field only
def bump_under_iter(c: Counter) -> int32:  # tpyc: mir(covered)
    t = 0
    for x in c.items:
        c.bump()
        t += x
    return t


# free conflict: a field view live across a method that replaces the field; sema is silent
# (BUGS.md#explicit-view-local-source-mutation-unguarded)
def view_across_rename(c: Counter, s: str, go: bool) -> int:  # tpyc: mir(conflict /replacement/)
    v: StrView = c.name
    if go:
        c.rename(s)
    return len(v)


# free safe sibling: the field view is dead before the replacing call
def view_before_rename(c: Counter, s: str) -> int:  # tpyc: mir(covered)
    v: StrView = c.name
    n = len(v)
    c.rename(s)
    return n


# kept refusal: a method's view result live across a method that replaces its source --
# the conflict needs the result's origin inside the receiver
def tag_across_rename(c: Counter, s: str, go: bool) -> int:  # tpyc: mir(uncovered /^call needs finalized known summary$/)
    v: StrView = c.tag()
    if go:
        c.rename(s)
    return len(v)


# aliased argument: the caller passes two records; the possible alias is the callee's
def call_merge(a: Counter, b: Counter) -> None:  # tpyc: mir(covered)
    a.merge_from(b)  # tpyc: mir_write(a.items[structure])


@dynamic
class Shape(Protocol):
    def grow(self) -> None: ...


class Base(Shape):
    items: list[int32]

    def __init__(self) -> None:
        self.items = []

    def grow(self) -> None:
        pass


class Sub(Base):
    def __init__(self) -> None:
        super().__init__()

    def grow(self) -> None:
        self.items.append(2)


# kept refusal: a virtual owner -- the static type does not name the body that runs
def call_virtual(b: Base) -> None:  # tpyc: mir(uncovered /^unsupported expression type$/)
    b.grow()


class Temp:
    deg: int32

    def __init__(self, deg: int32) -> None:
        self.deg = deg

    @property
    def twice(self) -> int32:
        return self.deg * 2

    @twice.setter
    def twice(self, v: int32) -> None:
        self.deg = v // 2


# kept refusal: a property read
def read_prop(t: Temp) -> int32:  # tpyc: mir(uncovered /^unsupported expression$/)
    return t.twice


# kept refusal: a property write
def write_prop(t: Temp, v: int32) -> None:  # tpyc: mir(uncovered /^unsupported expression type$/)
    t.twice = v


class Pair:
    a: int32

    def __init__(self, a: int32) -> None:
        self.a = a

    # dunder body: a dunder body borrows its receiver like any method body
    def __eq__(self, other: "Pair") -> bool:  # tpyc: mir(covered)
        return self.a == other.a

    # dunder body: a template-less dunder is a call target too
    def __bool__(self) -> bool:  # tpyc: mir(covered) mir_summary(known)
        return self.a != 0


# kept refusal: an explicit call of a dunder with an injected operator template
def call_dunder(p: Pair, q: Pair) -> bool:  # tpyc: mir(uncovered /^unsupported expression$/)
    return p.__eq__(q)


# free caller: an explicit call of a template-less dunder is an ordinary method call
def call_bool(p: Pair) -> bool:  # tpyc: mir(covered)
    return p.__bool__()


class Tally:
    n: int32

    # kept refusal: a constructor tail calling a method on self -- the record's definition
    # refuses a constructor with body effects, so the method's summary stays opaque
    def __init__(self, n: int32) -> None:  # tpyc: mir(uncovered /^call needs finalized known summary$/)
        self.n = n
        self.bump()

    def bump(self) -> None:  # tpyc: mir(covered) mir_summary(opaque /^summary record: constructor body effects$/)
        self.n += 1


class Outer:
    inner: Gauge

    def __init__(self, inner: Own[Gauge]) -> None:
        self.inner = inner


# kept refusal: a field receiver
def field_receiver(o: Outer) -> None:  # tpyc: mir(uncovered /^call needs borrowed record name$/)
    o.inner.bump()


# free body: a record parameter returned -- the summarized callee the next section's receiver calls
def pick(g: Gauge) -> Gauge:  # tpyc: mir(covered) mir_summary(known)
    return g


# kept refusal: a call-result receiver (the borrowed result of a summarized callee)
def result_receiver(g: Gauge) -> int32:  # tpyc: mir(uncovered /^call needs borrowed record name$/)
    return pick(g).read()


# kept refusal: a constructor temporary as the receiver
def temp_receiver(k: int32) -> int32:  # tpyc: mir(uncovered /^call needs borrowed record name$/)
    return Gauge(k).read()


# kept refusal: an element receiver
def element_receiver(gs: list[Gauge]) -> None:  # tpyc: mir(uncovered /^call needs borrowed record name$/)
    gs[0].bump()


class Cell[T]:
    val: T

    def __init__(self, val: Own[T]) -> None:
        self.val = val

    def get(self) -> T:
        return self.val


# kept refusal: a generic record's method; its generic record parameter refuses first
def generic_record(c: Cell[int32]) -> int32:  # tpyc: mir(uncovered /^unsupported parameter type$/)
    return c.get()


class Scaler:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    def same[T](self, x: T) -> T:
        return x

    # A bare `return self.k` stops the compiler (BUGS.md#consuming-method-scalar-field-read-crashes).
    def finish(self: Own[Self]) -> int32:
        return self.k + 1

    @staticmethod
    def double(x: int32) -> int32:
        return x * 2


# kept refusal: a generic method
def generic_method(s: Scaler) -> int32:  # tpyc: mir(uncovered /^unsupported expression$/)
    return s.same(s.k)


# kept refusal: a consuming method
def consuming(k: int32) -> int32:  # tpyc: mir(uncovered /^unsupported expression$/)
    s = Scaler(k)
    return s.finish()


# kept refusal: a staticmethod
def static_call(x: int32) -> int32:  # tpyc: mir(uncovered /^call needs resolved ordinary callee$/)
    return Scaler.double(x)


class Animal:
    legs: int32

    def __init__(self, legs: int32) -> None:
        self.legs = legs

    def add_leg(self) -> None:
        self.legs += 1


class Dog(Animal):
    def __init__(self) -> None:
        super().__init__(4)


# kept refusal: an inherited method (the owner has a parent); the parameter of a record
# with a parent refuses first
def inherited(d: Dog) -> None:  # tpyc: mir(uncovered /^unsupported parameter type$/)
    d.add_leg()


# kept refusal: a recursive method's caller
def call_countdown(c: Counter) -> int32:  # tpyc: mir(uncovered /^call needs finalized known summary$/)
    return c.countdown(3)


# kept refusal: a container field returned from the receiver refuses at the caller's binding
def call_all_items(c: Counter) -> int:  # tpyc: mir(uncovered /^unsupported reference fact$/)
    xs = c.all_items()
    xs.append(9)
    return len(xs)


def main() -> None:
    c = Counter("ctr")
    call_bump(c)
    call_push(c, 3)
    call_push(c, 4)
    print("call_bump/call_push:", c.n, len(c.items))
    call_set_at(c, 7)
    print("call_set_at:", c.items[0])
    print("call_total:", call_total(c))
    print("call_label:", call_label(c))
    call_in_if(c, True)
    call_in_while(c, 2)
    call_in_for(c)
    print("call_in_if/while/for:", c.n, len(c.items))
    print("call_readonly:", call_readonly(c))
    d = Counter("other")
    d.bump()
    d.bump()
    call_absorb(c, d)
    print("call_absorb:", c.n)
    print("call_ahead:", call_ahead(c, d), call_ahead(d, c))
    call_me(c)
    print("call_me:", c.n)
    print("call_me_ro:", call_me_ro(c))
    call_other(c, d)
    print("call_other:", c.n, d.n)
    print("call_first:", call_first(c, "word"))
    print("call_as_argument:", call_as_argument(c))
    push_own_total(c)
    print("push_own_total:", len(c.items), c.items[len(c.items) - 1])
    print("call_count_of:", call_count_of(c))
    g = Gauge(10)
    two_owners(c, g)
    print("two_owners:", c.n, g.level)
    c.bump_twice()
    print("bump_twice:", c.n)
    c.poke(d)
    print("poke:", c.n, d.n)
    print("Counter.grow_under_iter:", c.grow_under_iter(False), len(c.items))
    print("Counter.bump_under_iter:", c.bump_under_iter(), c.n)
    print("grow_under_iter:", grow_under_iter(c, False), len(c.items))
    push_free(c, 1)
    print("free_grow_under_iter:", free_grow_under_iter(c, False), len(c.items))
    print("bump_under_iter:", bump_under_iter(c), c.n)
    print("view_across_rename:", view_across_rename(c, "renamed", False), c.name)
    print("view_before_rename:", view_before_rename(c, "renamed"), c.name)
    print("tag_across_rename:", tag_across_rename(c, "again", False), c.name)
    d.push(8)
    call_merge(c, d)
    print("call_merge:", len(c.items), c.items[len(c.items) - 1])
    s = Sub()
    call_virtual(s)
    print("call_virtual:", len(s.items))
    t = Temp(5)
    write_prop(t, 12)
    print("read_prop/write_prop:", read_prop(t), t.deg)
    print("call_dunder:", call_dunder(Pair(1), Pair(1)), call_dunder(Pair(1), Pair(2)))
    print("call_bool:", call_bool(Pair(1)), call_bool(Pair(0)))
    print("ctor_call:", Tally(1).n)
    o = Outer(Gauge(1))
    field_receiver(o)
    print("field_receiver:", o.inner.level)
    print("result_receiver:", result_receiver(g))
    print("temp_receiver:", temp_receiver(7))
    gs = [Gauge(2), Gauge(3)]
    element_receiver(gs)
    print("element_receiver:", gs[0].level)
    print("generic_record:", generic_record(Cell(9)))
    sc = Scaler(3)
    print("generic_method:", generic_method(sc))
    print("consuming:", consuming(4))
    print("static_call:", static_call(5))
    dog = Dog()
    inherited(dog)
    print("inherited:", dog.legs)
    print("call_countdown:", call_countdown(c))
    print("call_all_items:", call_all_items(c), len(c.items))


main()
