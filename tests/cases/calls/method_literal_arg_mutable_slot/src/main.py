# A container LITERAL at a method / static-method parameter the signature
# spells MUTABLE -- because the callee mutates it, or because it lends it back
# through the return, which keeps the parameter non-const. A brace-init is a
# prvalue, so the in-place render the const slots take does not compile there;
# the literal hoists to a named temporary instead, the free-function family's
# render. The argument is a fresh temporary at every subject line, so there is
# no caller-side alias to observe: what each section prints is the callee's
# own view of the container it was handed. The other temporaries -- an owning
# call, an explicit copy -- take the same hoist at the same slots.
from tpy import Own, copy, int32, readonly


class Rec:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class K:
    tag: int32
    items: list[int32]

    def __init__(self) -> None:
        self.tag = 0
        self.items = []

    def fill(self, xs: list[int32]) -> int32:
        xs.append(9)
        return len(xs)

    def fill_recs(self, rs: list[Rec]) -> int32:
        rs.append(Rec(9))
        return rs[0].x + len(rs)

    def fill_dict(self, d: dict[str, int32]) -> int32:
        d["z"] = 9
        return len(d)

    def fill_set(self, s: set[int32]) -> int32:
        s.add(9)
        return len(s)

    def fill_nested(self, rows: list[list[int32]]) -> int32:
        rows.append([9])
        return len(rows)

    # returns the parameter, so the slot stays mutable although nothing in the
    # body writes through it
    def pick(self, xs: list[int32]) -> list[int32]:
        return xs

    # lends CALLER-visible storage back after absorbing the argument, so a
    # bound result that copied would miss the caller's later append
    def stash(self, xs: list[int32]) -> list[int32]:
        self.items.extend(xs)
        return self.items

    # reads only: the slot is const-inferred and keeps the in-place render
    def total(self, xs: list[int32]) -> int32:
        s = 0
        for v in xs:
            s += v
        return s

    def ro(self, xs: readonly[list[int32]]) -> int32:
        return len(xs)

    @staticmethod
    def fill_static(xs: list[int32]) -> int32:
        xs.append(9)
        return len(xs)

    @staticmethod
    def fill_static_dict(d: dict[str, int32]) -> int32:
        d["z"] = 9
        return len(d)


def sink(n: int32) -> int32:
    return n


def mk() -> Own[list[int32]]:
    return [1, 2]


# generic function with a concrete mutable slot beside the open one: the
# temp hoists there too
def push_gen[T](x: T, xs: list[int32]) -> int32:
    xs.append(9)
    return len(xs)


def push_free(xs: list[int32]) -> int32:
    xs.append(9)
    return len(xs)


def main() -> None:
    k = K()
    # method, named receiver
    print("meth_list", k.fill([1, 2]))  # tpyc: ok
    # method, record elements
    print("meth_recs", k.fill_recs([Rec(1), Rec(2)]))  # tpyc: ok
    # method, dict literal
    print("meth_dict", k.fill_dict({"a": 1}))  # tpyc: ok
    # method, set literal
    print("meth_set", k.fill_set({1, 2}))  # tpyc: ok
    # method, nested list literal
    print("meth_nested", k.fill_nested([[1], [2]]))  # tpyc: ok
    # method, temporary receiver
    print("temp_recv", K().fill([1, 2]))  # tpyc: ok
    # method whose slot stays mutable only because the return borrows it
    print("lend_back", len(k.pick([3, 4])))  # tpyc: ok
    # ... and the borrowed result BOUND: the declaration is a flush position
    # too, so the literal hoists to a named temporary the alias then outlives
    got = k.pick([3, 4])  # tpyc: ok
    got.append(5)
    print("lend_back_bound", got)
    # ... and bound from a callee that lends the receiver's own storage: the
    # alias sees the append, and the receiver's field shows it
    kept = k.stash([6])  # tpyc: ok
    kept.append(7)
    print("lend_back_stash", k.items)
    # an owning call and an explicit copy as the temporary: bound in place
    # at a const method slot, hoisted at a generic function's mutable slot
    # and at a free function's, the call's result bound to a local (the
    # static method's slot has no such row yet)
    print("own_call_const_meth", k.total(mk()))  # tpyc: ok
    print("own_call_generic", push_gen(1, mk()))  # tpyc: ok
    src: list[int32] = [1, 2, 3]
    n = push_free(copy(src))  # tpyc: ok
    print("copy_free_bound", n, src)
    # method call nested in another call's argument list
    print("nested_arg", sink(k.fill([1, 2])))  # tpyc: ok
    # static method (a qualified call, not a receiver call)
    print("static_list", K.fill_static([1, 2]))  # tpyc: ok
    print("static_dict", K.fill_static_dict({"a": 1}))  # tpyc: ok
    # the const-slot siblings: an inferred-const and a declared-readonly slot
    # both keep the in-place brace
    print("const_slot", k.total([1, 2, 3]))  # tpyc: ok
    print("readonly_slot", k.ro([4, 5]))  # tpyc: ok


main()
