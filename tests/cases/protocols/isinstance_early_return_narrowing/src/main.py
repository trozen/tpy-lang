# Early-return narrowing on polymorphic-class sources: `if not isinstance(p, Sub): return`
# (or `raise`) makes the implicit-else (code after the if) execute with p
# narrowed to Sub. Codegen extracts the dynamic_cast at the outer scope --
# `Sub& __p = *dynamic_cast<Sub*>(<cast_arg>)` -- mirroring the assert path.
# Covers both bare Polymorphic (`T&`) and Optional[Polymorphic] (`T*`) sources,
# runtime coverage of the guard-failing branches, sequential narrowings, and
# persistent narrowings inside loop / try / with / match-case bodies (which
# must not leak their cast-and-cache aliases past the closing brace).
from typing import Optional, Protocol
from tpy import dynamic, readonly


@dynamic
class Tagged(Protocol):
    pass


class Pet(Tagged):
    _name: str

    def __init__(self, n: str) -> None:
        self._name = n

    @readonly
    def name(self) -> str:
        return self._name


class Dog(Pet):
    def __init__(self, n: str) -> None:
        super().__init__(n)

    @readonly
    def bark(self) -> str:
        return "woof from " + self._name


class Cat(Pet):
    def __init__(self, n: str) -> None:
        super().__init__(n)


class WatchDog(Dog):
    def __init__(self, n: str) -> None:
        super().__init__(n)

    @readonly
    def alert(self) -> str:
        return "ALERT from " + self._name


class GuardDog(WatchDog):
    def __init__(self, n: str) -> None:
        super().__init__(n)

    @readonly
    def patrol(self) -> str:
        return "PATROL by " + self._name


def bare_negative(p: Pet) -> str:
    # Negative-guard early-return: post-guard p narrows to Dog via cast-and-cache
    # at the outer scope. Source is Pet& -- polymorphic_cast_arg emits &p.
    if not isinstance(p, Dog):  # tpyc: ok
        return "non-dog"
    narrowed = p  # tpyc: type(Dog)
    return narrowed.bark()


def bare_raise(p: Pet) -> str:
    # Same shape, raise instead of return.
    if not isinstance(p, Dog):  # tpyc: ok
        raise ValueError("expected Dog")
    narrowed = p  # tpyc: type(Dog)
    return narrowed.bark()


def bare_const(p: readonly[Pet]) -> str:
    # Const-borrow source -- post-guard cast emits `const Dog& __p = *dynamic_cast<const Dog*>(&p)`.
    if not isinstance(p, Dog):  # tpyc: ok
        return "non-dog"
    narrowed = p  # tpyc: type(Dog)
    return narrowed.bark()


def optional_negative(p: Optional[Pet]) -> str:
    # Optional-source: lowered to Pet*; cast input is `p` (already pointer).
    # The narrowed (Dog) post-guard path is comp-only -- constructing
    # Optional[Pet] from a Dog rvalue is Phase-20-rejected as slicing. The
    # guard-failing branch is reachable via a None literal and is exercised
    # at runtime below.
    if not isinstance(p, Dog):  # tpyc: ok
        return "none-or-non-dog"
    narrowed = p  # tpyc: type(Dog)
    return narrowed.bark()


def positive_then_more(p: Pet) -> str:
    # Positive guard with early return: the if-body returns. For the positive
    # case `if isinstance(p, Dog): return ...`, else_type_facts has no useful
    # narrowing for p (p stays Pet) -- no post-guard extraction emits.
    if isinstance(p, Dog):  # tpyc: ok
        return "DOG: " + p.bark()
    return "pet: " + p.name()


def sequential_negative(p: Pet) -> str:
    # Two chained negative-guard early-returns on the same variable. The first
    # cast-and-cache aliases p as `const Dog& __p`; the second binds to a fresh
    # `const WatchDog& __p_2` (suffix avoids C++ redecl), and post-second-guard
    # reads of p route to __p_2. The cast input for both stays anchored to the
    # original Pet& source via polymorphic_cast_arg, not chained through __p.
    if not isinstance(p, Dog):  # tpyc: ok
        return "non-dog"
    nd = p  # tpyc: type(Dog)
    if not isinstance(p, WatchDog):  # tpyc: ok
        return "DOG: " + nd.bark()
    nw = p  # tpyc: type(WatchDog)
    return "WATCH: " + nw.alert()


def triple_chain(p: Pet) -> str:
    # Three-level chain on the same variable exercises the multi-bump path
    # in `_fresh_alias_local`: `__p` -> `__p_2` -> `__p_3`.
    if not isinstance(p, Dog):  # tpyc: ok
        return "non-dog"
    if not isinstance(p, WatchDog):  # tpyc: ok
        return "dog only"
    if not isinstance(p, GuardDog):  # tpyc: ok
        return "watch only"
    g = p  # tpyc: type(GuardDog)
    return "guard: " + g.patrol()


def assert_then_assert(p: Pet) -> str:
    # Two persistent emits at the same C++ scope via the assert path. Both
    # route through _emit_isinstance_extractions with persistent=True, so
    # the second alias must bump to __p_2 -- same mechanism, different
    # caller from the early-return shape.
    assert isinstance(p, Dog)  # tpyc: ok
    assert isinstance(p, WatchDog)  # tpyc: ok
    w = p  # tpyc: type(WatchDog)
    return "ASSERT-WATCH: " + w.alert()


def assert_then_early_return(p: Pet) -> str:
    # Cross-call-site combination: assert (persistent) then early-return
    # (persistent). narrowed_vars state set by the assert must survive into
    # the early-return picker so its bump finds a fresh name.
    assert isinstance(p, Dog)  # tpyc: ok
    if not isinstance(p, WatchDog):  # tpyc: ok
        return "ASSERT-DOG: " + p.bark()
    w = p  # tpyc: type(WatchDog)
    return "ASSERT-EARLY-WATCH: " + w.alert()


def sibling_vars(p: Pet, q: Pet) -> str:
    # Two sibling polymorphic params each with chained narrowings. Without the
    # scope-global alias-name check (`_fresh_alias_local` queries
    # `declared_persistent_aliases`), q's first narrowing could pick a base
    # name (`__q`) that happens to collide with p's earlier bumped name --
    # or vice versa with `p`/`p_2` siblings both producing `__p_2`. C++
    # identifiers live in one scope-global namespace; the picker has to
    # respect that.
    if not isinstance(p, Dog):  # tpyc: ok
        return "p non-dog"
    if not isinstance(p, WatchDog):  # tpyc: ok
        return "p dog: " + p.bark()
    if not isinstance(q, Dog):  # tpyc: ok
        return "p watch / q non-dog"
    return "p watch / q dog: " + q.bark()


def narrow_in_for_body(p: Pet, n: int) -> str:
    # Polymorphic post-guard narrowing INSIDE a for body. The cast-and-cache
    # alias `__p` is declared inside the loop's `{...}` and narrowed_vars[p]
    # was leaking past the closing brace pre-fix. Post-fix, the snapshot at
    # `_gen_for_each` entry restores narrowed_vars after the body so the
    # post-loop read of `p` doesn't reference the out-of-scope alias.
    last = ""
    for _ in range(n):
        if not isinstance(p, Dog):
            return "non-dog in for"
        last = p.bark()
    return "for done: " + last + " / " + p.name()


def narrow_in_while_body(p: Pet) -> str:
    # `if not isinstance(p, Dog): return` inside a while body. Pre-fix, the
    # cast-and-cache `__p` was declared inside the while `{...}` but
    # narrowed_vars[p]='__p' leaked past the closing brace -- the post-loop
    # `return p.name()` would emit `__p.name()` referencing a now-undeclared
    # local. Now narrowed_vars is snapshotted around the body.
    count = 0
    while count < 1:
        if not isinstance(p, Dog):
            return "non-dog in loop"
        # narrowed_vars[p]='__p' visible HERE (inside body)
        count += 1
    # narrowed_vars[p] restored to pre-loop state
    return "loop done: " + p.name()


def narrow_in_try_body(p: Pet) -> str:
    # Persistent narrowing INSIDE a try body. The cast-and-cache alias
    # declared inside the C++ `try { ... }` block must not leak into the
    # except handler or post-try code -- the alias is out of scope there.
    try:
        if not isinstance(p, Dog):
            raise ValueError("not a dog")
        # narrowed_vars[p] aliased HERE inside the try body
        return "TRY-DOG: " + p.bark()
    except ValueError:
        # narrowed_vars restored before this scope -- p reads through Pet
        return "EXC: " + p.name()


def narrow_in_match_case(p: Pet, label: int) -> str:
    # Persistent narrowing INSIDE a match case body. Each case opens its own
    # C++ `case N: { ... }` scope; aliases from one case must not be visible
    # in sibling cases or post-match code.
    match label:
        case 1:
            if not isinstance(p, Dog):
                return "case1 non-dog"
            return "case1 dog: " + p.bark()
        case 2:
            if not isinstance(p, Dog):
                return "case2 non-dog"
            return "case2 dog2: " + p.bark()
        case _:
            return "default: " + p.name()


class CM:
    # Minimal context manager used by narrow_in_with_body. No state -- the
    # interesting part is the `with` block's C++ scope, not the manager.
    def __enter__(self) -> "CM":
        return self

    def __exit__(self, et, exc_val, etb) -> None:
        pass


def narrow_in_with_body(p: Pet) -> str:
    # Persistent narrowing INSIDE a `with` body. The body is wrapped in
    # `try { ... } catch (...) { __exit__; throw; }`; the cast-and-cache
    # alias declared inside the body must not leak into the post-with read.
    with CM():
        if not isinstance(p, Dog):
            return "with non-dog"
        result = "WITH-DOG: " + p.bark()
    # narrowed_vars restored after with-body; p reads through Pet here
    return result + " / pet: " + p.name()


def safe_raise(p: Pet) -> str:
    # Wrap bare_raise to exercise the raise path at runtime without aborting.
    try:
        return bare_raise(p)
    except ValueError as e:
        return "caught: " + str(e)


def main() -> None:
    d = Dog("rex")
    c = Cat("whiskers")
    w = WatchDog("rex-watch")

    # Narrowed-path coverage (Dog).
    print(bare_negative(d))
    print(bare_const(d))
    print(positive_then_more(d))

    # Guard-failing-branch coverage (non-Dog).
    print(bare_negative(c))
    print(bare_const(c))
    print(positive_then_more(c))

    # Raise path -- both branches.
    print(safe_raise(d))
    print(safe_raise(c))

    # Sequential narrowing: three runtime outcomes.
    print(sequential_negative(c))
    print(sequential_negative(d))
    print(sequential_negative(w))

    # Three-level chain: exercises `__p` -> `__p_2` -> `__p_3`.
    g = GuardDog("guard-rex")
    print(triple_chain(c))
    print(triple_chain(d))
    print(triple_chain(w))
    print(triple_chain(g))

    # Persistent + persistent across call sites (assert + assert, assert + early-return).
    print(assert_then_assert(w))
    print(assert_then_early_return(d))
    print(assert_then_early_return(w))

    # Optional[Pet] guard-failing branch -- None bypasses Phase-20 slicing.
    print(optional_negative(None))

    # Sibling-vars: cross-variable alias-name collision regression guard.
    print(sibling_vars(c, d))
    print(sibling_vars(d, c))
    print(sibling_vars(w, c))
    print(sibling_vars(w, d))

    # Narrowing inside loop bodies: narrowed_vars must NOT leak past the
    # closing brace of the for / while body.
    print(narrow_in_for_body(d, 2))
    print(narrow_in_for_body(c, 2))
    print(narrow_in_while_body(d))
    print(narrow_in_while_body(c))

    # Narrowing inside try / match-case / with bodies.
    print(narrow_in_try_body(d))
    print(narrow_in_try_body(c))
    print(narrow_in_match_case(d, 1))
    print(narrow_in_match_case(c, 1))
    print(narrow_in_match_case(d, 2))
    print(narrow_in_match_case(c, 99))
    print(narrow_in_with_body(d))



main()
