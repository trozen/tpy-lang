# Range advancement must ignore writes to the Python target binding.
# Fresh-target witnesses never read that target afterward: doing so hoists it.
import asyncio
from typing import Iterator

from tpy import AnyFixedInt, int32, int64, ReturnException, error_return, noalloc, nocopy


def original() -> None:
    # Free function: preserve the original early-exit regression.
    for i in range(5):  # tpyc: ok
        print(i)
        i = i + 100  # tpyc: ok
    print("done")


def steps32(base: int32, step: int32) -> None:
    # int32: all five emission arms keep a private induction value.
    count = 0
    total = 0
    for a in range(base, base + 5):  # tpyc: ok
        total += a - base
        a = base  # tpyc: ok
        count += 1
        if count > 10:
            break
    print("int32 +1", count, total)
    count = 0
    total = 0
    for b in range(base + 5, base, -1):  # tpyc: ok
        total += b - base
        b = base + 5  # tpyc: ok
        count += 1
        if count > 10:
            break
    print("int32 -1", count, total)
    count = 0
    total = 0
    for c in range(base, base + 10, 2):  # tpyc: ok
        total += c - base
        c = base  # tpyc: ok
        count += 1
        if count > 10:
            break
    print("int32 +2", count, total)
    count = 0
    total = 0
    for d in range(base + 10, base, -2):  # tpyc: ok
        total += d - base
        d = base + 10  # tpyc: ok
        count += 1
        if count > 10:
            break
    print("int32 -2", count, total)
    count = 0
    total = 0
    for e in range(base, base + 5 * step, step):  # tpyc: ok
        total += e - base
        e = base  # tpyc: ok
        count += 1
        if count > 10:
            break
    print("int32 variable", count, total)


def steps64(base: int64, step: int64) -> None:
    # int64: typed bounds force the wider counter instead of default int32.
    count = 0
    total: int64 = 0
    for a in range(base, base + 5):  # tpyc: ok
        total += a - base
        a = base  # tpyc: ok
        count += 1
        if count > 10:
            break
    print("int64 +1", count, total)
    count = 0
    total = 0
    for b in range(base + 5, base, -1):  # tpyc: ok
        total += b - base
        b = base + 5  # tpyc: ok
        count += 1
        if count > 10:
            break
    print("int64 -1", count, total)
    count = 0
    total = 0
    for c in range(base, base + 10, 2):  # tpyc: ok
        total += c - base
        c = base  # tpyc: ok
        count += 1
        if count > 10:
            break
    print("int64 +2", count, total)
    count = 0
    total = 0
    for d in range(base + 10, base, -2):  # tpyc: ok
        total += d - base
        d = base + 10  # tpyc: ok
        count += 1
        if count > 10:
            break
    print("int64 -2", count, total)
    count = 0
    total = 0
    for e in range(base, base + 5 * step, step):  # tpyc: ok
        total += e - base
        e = base  # tpyc: ok
        count += 1
        if count > 10:
            break
    print("int64 variable", count, total)


def steps_big(base: int, step: int) -> None:
    # BigInt: callers exercise both inline payloads and heap limbs.
    count = 0
    total: int = 0
    for a in range(base, base + 5):  # tpyc: ok
        total += a - base
        a = base  # tpyc: ok
        count += 1
        if count > 10:
            break
    print("BigInt +1", count, total)
    count = 0
    total = 0
    for b in range(base + 5, base, -1):  # tpyc: ok
        total += b - base
        b = base + 5  # tpyc: ok
        count += 1
        if count > 10:
            break
    print("BigInt -1", count, total)
    count = 0
    total = 0
    for c in range(base, base + 10, 2):  # tpyc: ok
        total += c - base
        c = base  # tpyc: ok
        count += 1
        if count > 10:
            break
    print("BigInt +2", count, total)
    count = 0
    total = 0
    for d in range(base + 10, base, -2):  # tpyc: ok
        total += d - base
        d = base + 10  # tpyc: ok
        count += 1
        if count > 10:
            break
    print("BigInt -2", count, total)
    count = 0
    total = 0
    for e in range(base, base + 5 * step, step):  # tpyc: ok
        total += e - base
        e = base  # tpyc: ok
        count += 1
        if count > 10:
            break
    print("BigInt variable", count, total)


def binding_forms(subject: int32) -> None:
    count = 0
    for a in range(5):
        # Augmented assignment must not cancel the iterator's increment.
        a -= 1  # tpyc: ok
        count += 1
        if count > 10:
            break
    print("augassign", count)
    count = 0
    for b in range(5):
        # Walrus writes occur in expression descendants.
        if (b := 0) == 0:  # tpyc: ok
            count += 1
        if count > 10:
            break
    print("walrus", count)
    total = 0
    for c in range(3):
        # Nested scalar reuse changes the outer target, not its cursor.
        for c in range(10, 12):  # tpyc: ok
            total += c
        total += c
    print("nested scalar", total)
    count = 0
    for d in range(3):
        # A match capture is a binding write even without assignment syntax.
        match subject:
            case d:  # tpyc: ok
                count += 1
        if count > 10:
            break
    print("match capture", count)
    count = 0
    for e in range(3):
        # Tuple assignment includes a scalar write to the range target.
        e, other = 10, 20  # tpyc: ok
        count += other - e
    print("tuple assignment", count)


def comprehension_forms() -> None:
    count = 0
    for a in range(3):
        # The comprehension's walrus belongs to the enclosing function.
        body_values = [(a := 10) for j in range(1)]  # tpyc: ok
        count += len(body_values)
    print("comprehension walrus", count)
    count = 0
    total = 0
    for b in range(3):
        # A guarded filter write executes only on the second inner element.
        filtered_values = [j for j in range(2) if j == 1 and (b := 10) == 10]  # tpyc: ok
        count += len(filtered_values)
        total += b
    print("comprehension filter", count, total)
    total = 0
    for c in range(3):
        # The comprehension induction name is local to its own scope.
        shadow_values = [c for c in range(2)]  # tpyc: ok
        total += c + len(shadow_values)
    print("comprehension shadow inverse", total)


class Worker:
    count: int

    def __init__(self) -> None:
        # Constructor: the field observes every iteration without hoisting i.
        self.count = 0
        for i in range(3):
            i += 10  # tpyc: ok
            self.count += 1

    def method(self) -> int:
        # Method: writable target and read-only companion use the same rule.
        count = 0
        for i in range(3):
            i += 10  # tpyc: ok
            count += 1
        for j in range(3):  # tpyc: ok
            count += j
        return count

    @staticmethod
    def static() -> int:
        # Static method: its target remains distinct from induction.
        count = 0
        for i in range(3):
            i += 10  # tpyc: ok
            count += 1
        return count


def closure_forms() -> None:
    # Closure body: the range is lowered inside the nested function.
    def inner() -> int:
        count = 0
        for i in range(3):
            i += 10  # tpyc: ok
            count += 1
        return count

    print("closure body", inner())
    count = 0
    for a in range(3):
        # A nested definition may write the enclosing target indirectly.
        def write() -> None:
            nonlocal a
            a += 10  # tpyc: ok

        write()
        count += 1
    print("closure nonlocal", count)
    total = 0
    for b in range(3):
        # Local assignment is rejected: BUGS.md#nested-local-shadow-requires-nonlocal.
        def local(b: int32) -> int32:  # tpyc: ok
            return b

        total += b + local(10)
    print("closure parameter inverse", total)


@nocopy
class Gate:
    exits: int

    def __init__(self) -> None:
        self.exits = 0

    def __enter__(self) -> int32:
        return 10

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.exits += 1
        return False


def context_forms() -> None:
    gate = Gate()
    count = 0
    # Context-manager body: cleanup observes the completed loop.
    with gate:
        for a in range(3):
            a += 10  # tpyc: ok
            count += 1
    print("context body", count, gate.exits)


def control_edges() -> None:
    count = 0
    cleaned = 0
    for a in range(3):
        # Continue must run finally before advancing the private cursor.
        try:
            count += 1
            continue
        finally:
            a += 10  # tpyc: ok
            cleaned += 1
    else:
        print("continue finally else", count, cleaned)
    count = 0
    cleaned = 0
    for b in range(5):
        try:
            count += 1
            if count == 2:
                break
        finally:
            b += 10  # tpyc: ok
            cleaned += 1
    else:
        print("break unexpected else")
    print("break finally", count, cleaned)
    old = 7
    # Existing storage retains its last write, including an empty successor.
    for old in range(3):
        old = 20  # tpyc: ok
    print("existing target", old)
    for old in range(0):  # tpyc: ok
        old = 99
    print("empty target", old)
    count = 0
    for fresh in range(3):
        fresh = 30  # tpyc: ok
        count += 1
    print("postloop target", count, fresh)


def parameter_target(target: int32) -> int32:
    # Parameter storage is already hoisted before entering the range.
    for target in range(3):
        target = 40  # tpyc: ok
    return target


def bound(events: list[int], tag: int, value: int) -> int:
    events.append(tag)
    return value


def bound_order(step: int) -> None:
    events: list[int] = []
    count = 0
    # Each bound is captured once, in Python's left-to-right order.
    for i in range(bound(events, 1, 0), bound(events, 2, 5), bound(events, 3, step)):  # tpyc: ok
        i = 10  # tpyc: ok
        count += 1
    print("bound order", events, count)


def generator() -> Iterator[int]:
    count = 0
    for i in range(3):
        # Suspension already preserves separate frame induction state.
        i = 10  # tpyc: ok
        yield i
        count += 1
        if count > 10:
            break


async def async_body() -> None:
    count = 0
    for i in range(3):
        # Await resumes with the independent iterator state intact.
        i = 10  # tpyc: ok
        await asyncio.sleep(0)
        count += 1
        if count > 10:
            break
    print("async", count)


class MarkerError(Exception, ReturnException):
    pass


@error_return(MarkerError)
def error_body() -> int:
    # Error-return body: the successful result counts every iteration.
    count = 0
    for i in range(3):
        i += 10  # tpyc: ok
        count += 1
    return count


def match_body(subject: int) -> None:
    # Match arm: a typed subject avoids the separate literal-subject gap.
    match subject:
        case 1:
            count = 0
            for i in range(3):
                i += 10  # tpyc: ok
                count += 1
            print("match arm", count)
        case _:
            print("match unexpected arm")


def generic_count[T: AnyFixedInt](values: list[T]) -> int:
    # len supplies a concrete counter: BUGS.md#generic-param-reassign-rejected.
    count = 0
    for i in range(len(values)):  # tpyc: ok
        i = 10  # tpyc: ok
        count += 1
    # Mutation through the parameter must reach the caller's original list.
    values.clear()  # tpyc: ok
    return count


def concrete_count(values: list[int32]) -> int:
    count = 0
    for i in range(len(values)):  # tpyc: ok
        i = 10  # tpyc: ok
        count += 1
    values.clear()  # tpyc: ok
    return count


@noalloc
def readonly32(stop: int32) -> int32:
    # No-write inverse: keep the direct, allocation-free fixed-int counter.
    total = 0
    for i in range(stop):  # tpyc: ok
        total += i
    return total


def readonly_big(start: int, stop: int) -> int:
    # No-write heap BigInt inverse: no per-iteration target copy is needed.
    count = 0
    for i in range(start, stop):  # tpyc: ok
        if i >= start:
            count += 1
    return count


@nocopy
class Item:
    value: int

    def __init__(self, value: int) -> None:
        self.value = value


def container_inverse() -> None:
    values = [0, 1, 2]
    count = 0
    for value in values:
        # Scalar container iteration already advances an independent cursor.
        value = 10  # tpyc: ok
        count += 1
    print("scalar container inverse", count, values)
    items = [Item(1), Item(2)]
    for item in items:
        # Mutating the borrowed element must reach the original container.
        item.value += 10  # tpyc: ok
    print("reference container inverse", items[0].value, items[1].value)


def main() -> None:
    original()
    steps32(0, 2)
    steps32(0, -2)
    steps64(0, 2)
    steps64(0, -2)
    steps_big(0, 2)
    steps_big(0, -2)
    heap = int(1) << 100
    steps_big(heap, 2)
    steps_big(-heap, -2)
    binding_forms(10)
    comprehension_forms()
    worker = Worker()
    print("constructor", worker.count)
    print("method", worker.method())
    print("staticmethod", Worker.static())
    closure_forms()
    context_forms()
    control_edges()
    print("parameter target", parameter_target(7))
    bound_order(1)
    count = 0
    total = 0
    for value in generator():
        count += 1
        total += value
    print("generator", count, total)
    asyncio.run(async_body())
    try:
        print("error_return", error_body())
    except MarkerError:
        print("error_return unexpected error")
    match_body(1)
    generic_values = [0, 1, 2]
    concrete_values = [0, 1, 2]
    print("generic twin", generic_count(generic_values), concrete_count(concrete_values))
    print("generic twin aliases", len(generic_values), len(concrete_values))
    print("readonly noalloc", readonly32(5))
    print("readonly heap", readonly_big(heap, heap + 5))
    container_inverse()


# Module statement: only the count escapes, keeping the target unhoisted.
module_count = 0
for module_target in range(3):
    module_target += 10  # tpyc: ok
    module_count += 1
print("module", module_count)

main()
