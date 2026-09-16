# Callable element types supply lambda context at insertion and replacement.
# Reference arguments must retain caller-visible mutation through stored callbacks.
import asyncio
from typing import Callable, Iterator
from tpy import Array, Own, ReturnException, error_return, int32


def named(x: int32) -> int32:
    return x + 1


def consume(f: Own[Callable[[int32], int32]]) -> int32:
    return f(4)


def free_bindings():
    callbacks: list[Callable[[int32], int32]] = []
    # The consuming append/insert parameters retain the callable shape.
    callbacks.append(lambda x: x + 2)  # tpyc: ok
    callbacks.insert(0, lambda x: x * 3)  # tpyc: ok
    callbacks.append(named)  # tpyc: ok
    callbacks[1] = lambda x: x + 5  # tpyc: ok
    print("free", callbacks[0](2), callbacks[1](2), callbacks[2](2))
    print("own-param", consume(lambda x: x + 6))  # tpyc: ok

    commands: dict[str, Callable[[int32], int32]] = {}
    # Dictionary insertion and replacement use the same callable element context.
    commands["run"] = lambda x: x + 7  # tpyc: ok
    commands.setdefault("default", lambda x: x * 2)  # tpyc: ok
    commands["run"] = named  # tpyc: ok
    print("dict", commands["run"](2), commands["default"](3))

    pairs: list[tuple[Callable[[int32], int32], int32]] = []
    # Own around the tuple must not hide its per-element callable hints.
    pairs.append((lambda x: x + 8, 9))  # tpyc: ok
    pairs[0] = (lambda x: x + 10, 11)  # tpyc: ok
    cb, number = pairs[0]
    print("tuple", cb(number))
    print("tuple-index", pairs[0][0](pairs[0][1]))  # tpyc: ok

    optional: list[Callable[[int32], int32] | None] = []
    optional.append(lambda x: x + 12)  # tpyc: ok
    optional[0] = lambda x: x + 13  # tpyc: ok
    cb_opt = optional[0]
    if cb_opt is not None:
        print("optional", cb_opt(1))
    optional[0] = None  # tpyc: ok
    print("none", optional[0] is None)
    # Reassignment must copy the whole optional, including its empty state.
    cb_opt = optional[0]  # tpyc: ok
    print("optional-reassign", cb_opt is None)

    pending: dict[str, Callable[[int32], int32] | None] = {}
    pending["ready"] = lambda x: x + 14  # tpyc: ok
    pending["empty"] = None  # tpyc: ok
    pending["copy"] = pending["ready"]  # tpyc: ok
    ready = pending["copy"]
    if ready is not None:
        print("optional-dict", ready(1))
    # Dictionary views count populated and empty optional callback slots alike.
    print("optional-views", len(pending.values()), len(pending.items()))  # tpyc: ok

    fixed: Array[Callable[[int32], int32], 2] = [named, named]
    fixed[0] = lambda x: x + 15  # tpyc: ok
    print("array", fixed[0](1), fixed[1](1))

    mutators: list[Callable[[list[int32]], int32]] = []
    mutators.append(lambda xs: xs.pop())  # tpyc: ok
    values = [1, 2, 3]
    print("reference", mutators[0](values), len(values))


class Registry:
    callbacks: list[Callable[[int32], int32]]

    def __init__(self, offset: int32):
        self.callbacks = []
        # Constructor captures use the same escaping callable context.
        self.callbacks.append(lambda x: x + offset)  # tpyc: ok

    def replace(self):
        # Field receivers share the list element-write path.
        self.callbacks[0] = lambda x: x + 20  # tpyc: ok

    def invoke(self, x: int32) -> int32:
        callbacks = self.callbacks
        return callbacks[0](x)


def gen() -> Iterator[int32]:
    callbacks: list[Callable[[int32], int32]] = []
    callbacks.append(lambda x: x + 30)  # tpyc: ok
    yield callbacks[0](1)
    callbacks[0] = lambda x: x + 31  # tpyc: ok
    yield callbacks[0](1)


async def async_bindings() -> int32:
    callbacks: list[Callable[[int32], int32]] = []
    callbacks.append(lambda x: x + 40)  # tpyc: ok
    await asyncio.sleep(0)
    callbacks[0] = lambda x: x + 41  # tpyc: ok
    return callbacks[0](1)


def nested_bindings():
    def inner() -> int32:
        callbacks: list[Callable[[int32], int32]] = []
        callbacks.append(lambda x: x + 50)  # tpyc: ok
        return callbacks[0](1)
    print("closure", inner())


def branching_bindings(flag: int32):
    callbacks: list[Callable[[int32], int32]] = []
    try:
        callbacks.append(lambda x: x + 60)  # tpyc: ok
    finally:
        callbacks[0] = lambda x: x + 61  # tpyc: ok
    match flag:
        case 1:
            callbacks[0] = lambda x: x + 62  # tpyc: ok
        case _:
            pass
    print("branch", callbacks[0](1))

    with open("callbacks.txt", "w") as stream:
        callbacks.append(lambda x: x + 80)  # tpyc: ok
        stream.write("ok")
        print("context", callbacks[1](1))

    # Each comprehension element has the annotated callable context.
    generated: list[Callable[[int32], int32]] = [
        lambda x: x + 90 for _ in range(2)  # tpyc: ok
    ]
    print("comprehension", generated[0](1), generated[1](2))
    generated_dict: dict[str, Callable[[int32], int32]] = {
        "run": lambda x: x + 91 for _ in range(1)  # tpyc: ok
    }
    print("dict-comprehension", generated_dict["run"](1))


class Failure(Exception, ReturnException):
    pass


@error_return(Failure)
def error_return_binding() -> int32:
    callbacks: list[Callable[[int32], int32]] = []
    callbacks.append(lambda x: x + 100)  # tpyc: ok
    return callbacks[0](1)


def main():
    free_bindings()
    registry = Registry(10)
    print("constructor", registry.invoke(1))
    registry.replace()
    print("method", registry.invoke(1))
    for value in gen():
        print("generator", value)
    print("async", asyncio.run(async_bindings()))
    nested_bindings()
    branching_bindings(1)
    try:
        result = error_return_binding()
        print("error-return", result)
    except Failure:
        print("error-return", "unexpected")


# Module-level insertion is a distinct lowering position.
global_callbacks: list[Callable[[int32], int32]] = []
global_callbacks.append(lambda x: x + 70)  # tpyc: ok
print("global", global_callbacks[0](1))
main()
