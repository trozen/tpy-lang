# Regression: an rvalue-temporary arg bound to a borrowing coro param must be
# hoisted into the awaiter's frame, not flushed as a local of the suspending
# `case` block. Each coro READS the borrowed arg AFTER a suspension point --
# the read dereferences the frame-stored borrow, which dangled before the fix
# (silent UB for the union / pointer-form-Optional shapes; a hard C++ error for
# the plain non-value ref shape). Covers all three borrowing param shapes plus
# the negative cases (None and a stable lvalue must NOT be hoisted), a mixed
# borrowed/non-borrowed signature, and a lift inside a loop body.
import asyncio


class Dog:
    def __init__(self, name: str) -> None:
        self.name = name


class Cat:
    def __init__(self, name: str) -> None:
        self.name = name


async def via_union(a: Dog | Cat) -> str:
    await asyncio.sleep(0)
    if isinstance(a, Dog):
        return a.name
    return "cat"


async def via_optional(a: Dog | None) -> str:
    await asyncio.sleep(0)
    if a is not None:
        return a.name
    return "none"


async def via_ref(a: Dog) -> str:
    await asyncio.sleep(0)
    return a.name


# Mixed signature: the str arg (position 0) is a by-value param and is not
# hoisted; only the borrowing union arg (position 1) is. Guards the per-index
# `_param_borrows` skip / off-by-one.
async def via_mixed(tag: str, a: Dog | Cat) -> str:
    await asyncio.sleep(0)
    if isinstance(a, Dog):
        return tag + ":" + a.name
    return tag + ":cat"


async def main() -> None:
    print(await via_union(Dog("rex")))
    print(await via_union(Cat("tom")))
    print(await via_optional(Dog("fido")))
    print(await via_ref(Dog("spot")))
    # None must NOT be hoisted (lowers to nullptr, not a borrow).
    print(await via_optional(None))
    # A stable lvalue must NOT be hoisted: its address already persists, and
    # hoisting would copy it (breaking @nocopy / mutation-through-borrow).
    held = Dog("held")
    print(await via_ref(held))
    # Mixed borrowed/non-borrowed params.
    print(await via_mixed("tag", Dog("max")))
    # Lift inside a loop body: each suspension gets a distinct frame slot.
    for i in range(2):
        print(await via_ref(Dog("loop")))


asyncio.run(main())
