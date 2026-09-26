# Materializing an iterator that lends reference elements out of named storage
# warns (TPy copies where CPython aliases); sections print before any mutation.
from typing import Iterator
from tpy import Own, ValueType, copy, copy_iter


class C:
    def __init__(self, v: int) -> None:
        self.v = v


class Bag:
    cs: list[C]

    def __init__(self) -> None:
        self.cs = [C(7), C(8)]

    def each(self) -> Iterator[C]:
        for c in self.cs:
            yield c

    def field_source(self) -> None:
        # the source is a field read in a method
        a = list(reversed(self.cs))  # tpyc: warning(/copies C elements/)
        print("field", a[0].v)


def keep(c: C) -> bool:
    return c.v > 1


def fresh(cs: list[C]) -> Iterator[Own[C]]:
    for c in cs:
        yield copy(c)


def make() -> Own[list[C]]:
    return [C(3), C(4)]


# the source is a module-level global
GS = [C(30), C(31)]
GR = list(reversed(GS))  # tpyc: warning(/copies C elements/)
print("global", GR[0].v)


def param_source(cs: list[C]) -> None:
    # the source is a parameter
    a = list(reversed(cs))  # tpyc: warning(/copies C elements/)
    print("param", a[0].v)


def generator_body(cs: list[C]) -> Iterator[int]:
    # the copy happens inside a generator body
    a = list(reversed(cs))  # tpyc: warning(/copies C elements/)
    yield a[0].v


def named_adapter() -> None:
    ns = [1, 2]
    cs = [C(1), C(2)]
    # a dead adapter moves only itself: its elements still come out of cs
    z = zip(ns, cs)
    a = list(z)  # tpyc: warning(/copies tuple\[int32, C\] elements/)
    print("named_zip", a[1][1].v)
    it = reversed(cs)
    r = list(it)  # tpyc: warning(/copies C elements/)
    print("named_reversed", r[0].v)
    # the C half of a named zip over a temporary lends nothing named
    t = zip(ns, [C(5), C(6)])
    q = list(t)  # tpyc: ok
    print("named_zip_temp", q[0][1].v)
    # a second name for the adapter lends what the adapter does; its render
    # `auto w = y;` copies the position (BUGS.md#iterator-alias-copies-position)
    y = reversed(cs)
    w = y
    s = list(w)  # tpyc: warning(/copies C elements/)
    print("named_alias", s[0].v)
    # ... and so does an unpack target bound to one
    v, n = reversed(cs), 1
    u = list(v)  # tpyc: warning(/copies C elements/)
    print("named_unpack", u[0].v, n)


def generic_reversed[T](xs: list[T]) -> Own[list[T]]:
    # a generic payload hedges, as list(xs) does in a generic body
    return list(reversed(xs))  # tpyc: warning(/may copy T elements if not a value type/)


def generic_pending[T](xs: list[T]) -> Own[list[T]]:
    # the same hedge while the generator's facts are still pending
    return list(later_t(xs))  # tpyc: warning(/may copy T elements if not a value type/)


def generic_value_bound[T: ValueType](xs: list[T]) -> Own[list[T]]:
    # a value-bound payload copies nothing observable
    return list(reversed(xs))  # tpyc: ok


def generic() -> None:
    cs = [C(1), C(2)]
    ns = [3, 4]
    print("generic", generic_reversed(cs)[0].v, generic_pending(cs)[0].v,
          generic_value_bound(ns)[0])


def warned() -> None:
    ns = [1, 2]
    cs = [C(1), C(2)]
    d = {1: C(10), 2: C(20)}
    b = Bag()
    # zip / enumerate: the tuple's C half comes out of cs
    a = list(zip(ns, cs))  # tpyc: warning(/copies tuple\[int32, C\] elements/)
    print("zip", a[1][1].v)
    e = list(enumerate(cs))  # tpyc: warning(/copies tuple\[int32, C\] elements/)
    print("enumerate", e[1][1].v)
    # dict(...) over the same combinators copies the C values
    dz = dict(zip(ns, cs))  # tpyc: warning(/copies .* elements/)
    print("dict_zip", dz[2].v)
    de = dict(enumerate(cs))  # tpyc: warning(/copies .* elements/)
    print("dict_enumerate", de[0].v)
    # dict views lend the dict's values
    it = list(d.items())  # tpyc: warning(/copies tuple\[int32, C\] elements/)
    print("items", it[0][1].v)
    vs = list(d.values())  # tpyc: warning(/copies C elements/)
    print("values", vs[1].v)
    # filter / reversed lend the elements they pass on
    f = list(filter(keep, cs))  # tpyc: warning(/copies C elements/)
    print("filter", f[0].v)
    r = list(reversed(cs))  # tpyc: warning(/copies C elements/)
    print("reversed", r[0].v)
    # a generator defined BELOW this use: decided once its facts are known
    g = list(later(cs))  # tpyc: warning(/copies C elements/)
    print("generator", g[0].v)
    m = list(b.each())  # tpyc: warning(/copies C elements/)
    print("method_generator", m[1].v)
    # a generator expression lends what it iterates
    x = list(c for c in cs)  # tpyc: warning(/copies C elements/)
    print("genexpr", x[0].v)
    # nested: the inner zip lends cs through the outer one
    q = list(zip(ns, zip(ns, cs)))  # tpyc: warning(/copies .* elements/)
    print("nested_zip", q[1][1][1].v)
    # the other Iterable[Own[T]] sinks: += and slice assignment
    xs = [C(0)]
    xs += reversed(cs)  # tpyc: warning(/copies C elements/)
    xs[0:1] = reversed(cs)  # tpyc: warning(/copies C elements/)
    print("sinks", len(xs), xs[0].v)


def quiet() -> None:
    ns = [1, 2]
    cs = [C(1), C(2)]
    # the C half comes out of a temporary list, which nothing else sees
    a = list(zip(ns, [C(5), C(6)]))  # tpyc: ok
    print("zip_temp", a[0][1].v)
    q = list(zip(ns, zip(ns, [C(5), C(6)])))  # tpyc: ok
    print("nested_zip_temp", q[1][1][1].v)
    # value-only elements copy nothing observable
    e = list(enumerate(ns))  # tpyc: ok
    z = list(zip(ns, ns))  # tpyc: ok
    print("values_only", e[1][1], z[1][0])
    # a generator yielding owned elements hands over fresh copies
    o = list(fresh(cs))  # tpyc: ok
    print("owned_generator", o[0].v)


def acknowledged() -> None:
    ns = [1, 2]
    cs = [C(1), C(2)]
    d = {1: C(10), 2: C(20)}
    # copy_iter() makes each copy explicit, over every source kind
    a = list(copy_iter(zip(ns, cs)))  # tpyc: ok
    e = list(copy_iter(enumerate(cs)))  # tpyc: ok
    dz = dict(copy_iter(zip(ns, cs)))  # tpyc: ok
    it = list(copy_iter(d.items()))  # tpyc: ok
    vs = list(copy_iter(d.values()))  # tpyc: ok
    f = list(copy_iter(filter(keep, cs)))  # tpyc: ok
    r = list(copy_iter(reversed(cs)))  # tpyc: ok
    g = list(copy_iter(later(cs)))  # tpyc: ok
    q = list(copy_iter(zip(ns, zip(ns, cs))))  # tpyc: ok
    # a container temporary is owned by the adapter and iterated
    t = list(copy_iter(make()))  # tpyc: ok
    u = [C(0)]
    u.extend(copy_iter(make()))  # tpyc: ok
    print("copy_iter", a[0][1].v, e[1][1].v, dz[1].v, it[1][1].v, vs[0].v)
    print("copy_iter", f[0].v, r[0].v, g[1].v, q[0][1][1].v, t[1].v, u[2].v)


def later(cs: list[C]) -> Iterator[C]:
    for c in cs:
        yield c


def later_t[T](xs: list[T]) -> Iterator[T]:
    for x in xs:
        yield x


def main() -> None:
    warned()
    quiet()
    acknowledged()
    Bag().field_source()
    param_source([C(40), C(41)])
    for v in generator_body([C(50), C(51)]):
        print("generator_body", v)
    named_adapter()
    generic()


main()
