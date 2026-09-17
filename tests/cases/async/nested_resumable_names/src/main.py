# Nested resumable frames keep owner paths distinct at declarations and consumers.
# Receiver mutation across coroutine creation and generator yields must stay shared.
import asyncio
from typing import Iterator
from helpers import Library as Remote


class Outer:
    class Box[T]:
        value: T

        def __init__(self, value: T) -> None:
            # Unconstrained T may require a copy into inline field storage.
            self.value = value  # tpyc: warning(/may copy T into field/)

        async def get(self) -> T:
            await asyncio.sleep(0)
            return self.value

    class Inner:
        value: int

        def __init__(self, value: int) -> None:
            self.value = value

        async def compute(self, delta: int) -> int:
            await asyncio.sleep(0)
            self.value += delta
            return self.value

        async def echo[T](self, value: T) -> T:
            await asyncio.sleep(0)
            return value

        async def cleanup(self) -> int:
            try:
                return await self.compute(1)  # tpyc: ok
            finally:
                self.value += 1

        def values(self) -> Iterator[int]:
            # Two yields require a named resumable frame.
            yield self.value  # tpyc: ok
            yield self.value  # tpyc: ok

        def simple(self) -> Iterator[int]:
            # A single-yield loop generator lowers on the same named frame.
            for n in range(2):
                yield self.value + n  # tpyc: ok

    class Gate:
        value: int

        def __init__(self) -> None:
            self.value = 0

        async def __aenter__(self) -> int:
            await asyncio.sleep(0)
            self.value += 1
            return self.value

        async def __aexit__(self, et: None, ev: None, tb: None) -> None:
            await asyncio.sleep(0)
            self.value += 100

    class Layer:
        class Deep:
            async def compute(self) -> int:
                return 201

    class Layer_Deep:
        async def compute(self) -> int:
            return 202


class Outer_Layer:
    class Deep:
        async def compute(self) -> int:
            return 203


class Outer_Layer_Deep:
    async def compute(self) -> int:
        return 204


async def Outer_Inner_echo() -> int:
    return 205


def delegated(inner: Outer.Inner) -> Iterator[int]:
    # A yield in the loop embeds the nested method's concrete frame.
    for value in inner.values():  # tpyc: ok
        yield value
    # Keep the consumer on the named resumable route too.
    yield -1


async def nested_compute(inner: Outer.Inner, delta: int = 1) -> int:
    # The default-bearing factory signature needs Outer to be complete.
    return await inner.compute(delta)  # tpyc: ok


async def nested_echo[T](inner: Outer.Inner, value: T, delta: int = 1) -> T:
    # The generic twin must retain its template header and default argument.
    await inner.compute(delta)  # tpyc: ok
    return value


async def async_sections() -> None:
    # Bound coroutine: a later receiver mutation must reach the captured receiver.
    inner = Outer.Inner(1)
    pending = inner.compute(1)  # tpyc: ok
    inner.value = 40
    print("bound:", await pending, inner.value)  # tpyc: ok

    # Direct await: the sub-frame is declared in the module, outside Outer.
    print("await:", await inner.compute(1), inner.value)  # tpyc: ok

    # Generic method: template arguments attach to the same nested identity.
    print("generic:", await inner.echo(7))  # tpyc: ok

    # Generic nested class: concrete owner arguments survive frame qualification.
    box = Outer.Box(7)
    boxed = box.get()  # tpyc: ok
    box.value = 8
    print("generic class:", await boxed, box.value)  # tpyc: ok

    # Imported alias: frames belong to the helper's custom module namespace.
    remote = Remote.Worker(10)
    imported = remote.compute(1)  # tpyc: ok
    remote.value = 20
    print("imported bound:", await imported, remote.value)  # tpyc: ok
    print("imported await:", await remote.compute(1), remote.value)  # tpyc: ok

    # Async context manager: entry and exit both act on the original receiver.
    gate = Outer.Gate()
    async with gate as entered:  # tpyc: ok
        gate.value += 10
        print("context body:", entered, gate.value)
    print("context exit:", gate.value)

    # Finally: the nested method's cleanup keeps the receiver aliased too.
    try:
        print("try:", await inner.cleanup())  # tpyc: ok
    finally:
        print("finally:", inner.value)

    # Async generator consumer: the source stays shared across await boundaries.
    for value in inner.values():  # tpyc: ok
        print("async iteration:", value)
        inner.value += 10
        await asyncio.sleep(0)
    print("async iteration receiver:", inner.value)

    # Owner paths that flatten to identical underscore-separated names coexist.
    deep = Outer.Layer.Deep()
    joined = Outer.Layer_Deep()
    split = Outer_Layer.Deep()
    flat = Outer_Layer_Deep()
    print("deep:", await deep.compute())  # tpyc: ok
    print("joined:", await joined.compute())  # tpyc: ok
    print("split:", await split.compute())  # tpyc: ok
    print("flat:", await flat.compute())  # tpyc: ok
    # The flat free-function name must not collide with Inner.echo's frame.
    print("free:", await Outer_Inner_echo())  # tpyc: ok

    # Free async: the nested parameter aliases across factory creation and await.
    free_pending = nested_compute(inner)  # tpyc: ok
    inner.value = 70
    print("free bound:", await free_pending, inner.value)  # tpyc: ok
    print("free await:", await nested_compute(inner, 2), inner.value)  # tpyc: ok

    # Generic/default twin: both factory spellings retain the same receiver.
    generic_pending = nested_echo(inner, 7)  # tpyc: ok
    inner.value = 80
    print("generic free bound:", await generic_pending, inner.value)  # tpyc: ok
    print("generic free await:", await nested_echo(inner, 9, 2), inner.value)  # tpyc: ok


def main() -> None:
    # Named generator: mutation before and between next() calls stays visible.
    inner = Outer.Inner(1)
    values = inner.values()  # tpyc: ok
    inner.value = 50
    try:
        print("generator first:", next(values))
        inner.value = 60
        print("generator second:", next(values))
    except StopIteration:
        print("generator: unexpected stop")

    # Single-yield generator: the receiver must alias through its frame capture as well.
    for value in inner.simple():  # tpyc: ok
        print("simple:", value)
        inner.value += 1

    # Delegated generator: the embedded source observes each receiver mutation.
    for value in delegated(inner):  # tpyc: ok
        print("delegated:", value)
        # A scalar field write preserves the record retained by the frame.
        inner.value += 10  # tpyc: ok
    print("delegated receiver:", inner.value)

    asyncio.run(async_sections())


main()
