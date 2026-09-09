# Async declarations must build without importing an executor or coroutine runtime.
# Loading these definitions exercises header dependencies without creating coroutines.
from helper import loaded


# Free function: scalar, tuple and optional return signatures share the dependency.
async def scalar(n: int) -> int:  # tpyc: ok
    return n


async def wrapped(n: tuple[int]) -> tuple[int]:  # tpyc: ok
    return n


async def optional(n: int | None) -> int | None:  # tpyc: ok
    return n


# Generic twin: its template frame still needs the coroutine runtime header.
async def identity[T](n: T) -> T:  # tpyc: ok
    return n


# Method and nested-record method: the defining module owns both frame dependencies.
class Worker:
    async def compute(self, n: int) -> int:  # tpyc: ok
        return n


class Outer:
    class Inner:
        async def compute(self, n: int) -> int:  # tpyc: ok
            return n


# Context-manager methods: no async-with caller is needed to require these frames.
class Gate:
    async def __aenter__(self) -> int:  # tpyc: ok
        return 7

    async def __aexit__(self, et: None, ev: None, tb: None) -> None:  # tpyc: ok
        pass


# Async body: chained awaits instantiate the generic twin without an executor import.
async def chained(n: int) -> int:
    value = await scalar(n)  # tpyc: ok
    return await identity(value)  # tpyc: ok


# Try/finally: the cleanup region keeps the same enclosing frame dependency.
async def cleanup(n: int) -> int:
    try:
        return await chained(n)  # tpyc: ok
    finally:
        print("try/finally: cleanup")


def main() -> None:
    print("free: loaded")
    print("generic: loaded")
    print("method: loaded")
    print("nested record: loaded")
    print("context manager: loaded")
    print("chained await: loaded")
    print("try/finally: loaded")
    print("imported module:", loaded())


main()
