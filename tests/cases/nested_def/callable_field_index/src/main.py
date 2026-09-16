# A field can hold a container of functions: App.commands maps "inc" to inc,
# so app.commands["inc"](1) must fetch that function and call it, returning 2.
# The brackets select a stored function; they are not generic method type arguments.
# Test this with dict/list fields, nested fields and a property returning a list.
#
# The remaining sections exercise these calls in different statement contexts.
# Side effects verify that getters and index expressions run once, and that
# short-circuited calls do not evaluate their arguments. Mutating a container
# alias or a list passed to a callback must affect the original object.
# Lists created at the call site must also work as mutable callback arguments.
# Calls that may mutate a list must invalidate earlier proofs that an index is in range.
import asyncio
from typing import Callable, Iterator
from tpy import Own, ReturnException, error_return, int32, readonly


def inc(x: int32) -> int32:
    return x + 1


def touch(values: list[int32]) -> int32:
    values.append(7)
    return len(values)


def notify(x: int32):
    print("notify", x)


def make_values() -> Own[list[int32]]:
    return [1, 2]


def key(events: list[int32]) -> str:
    events.append(1)
    return "inc"


def arg(events: list[int32]) -> int32:
    events.append(2)
    return 8


class App:
    commands: dict[str, Callable[[int32], int32]]
    callbacks: list[Callable[[int32], int32]]
    groups: list[list[Callable[[int32], int32]]]
    writers: dict[str, Callable[[list[int32]], int32]]
    notifications: dict[str, Callable[[int32], None]]
    direct_writer: Callable[[list[int32]], int32]

    def __init__(self):
        self.commands = {"inc": inc}
        self.callbacks = [inc]
        self.groups = [[inc]]
        self.writers = {"touch": touch}
        self.notifications = {"run": notify}
        self.direct_writer = touch
        # Constructor invokes the indexed field through its inferred signature.
        print("constructor", self.commands["inc"](1))  # tpyc: ok

    @readonly
    def read(self) -> int32:
        # Calling a stored callback does not mutate its containing receiver.
        return self.callbacks[0](2)  # tpyc: ok

    def write(self, values: list[int32]) -> int32:
        return touch(values)

    @property
    def selected(self) -> list[Callable[[int32], int32]]:
        # Properties cannot mutate self; output makes repeated evaluation observable.
        print("property-getter")
        return self.callbacks


class Holder:
    app: App

    def __init__(self):
        self.app = App()


class Generic[T]:
    callbacks: list[Callable[[T], T]]

    def __init__(self, fn: Callable[[T], T]):
        self.callbacks = [fn]

    def invoke(self, x: T) -> T:
        return self.callbacks[0](x)  # tpyc: ok


def generate(app: App) -> Iterator[int32]:
    yield app.commands["inc"](3)  # tpyc: ok
    yield app.callbacks[0](4)  # tpyc: ok


async def async_read(app: App) -> int32:
    return app.commands["inc"](5)  # tpyc: ok


def mutate_then_read(app: App, values: list[int32]):
    for i in range(len(values)):
        print("bounds-before", values[i])  # tpyc: bounds_safe(values)
        app.writers["touch"](values)
        # The opaque callback can change the length, invalidating the guard.
        print("bounds", values[i])  # tpyc: bounds_checked(values)
        break
    for i in range(len(values)):
        print("direct-before", values[i])  # tpyc: bounds_safe(values)
        app.direct_writer(values)
        # Direct callable fields must invalidate the same argument facts.
        print("direct-after", values[i])  # tpyc: bounds_checked(values)
        break
    for i in range(len(values)):
        print("method-before", values[i])  # tpyc: bounds_safe(values)
        app.write(values)
        # Ordinary methods use the shared argument invalidation as well.
        print("method-after", values[i])  # tpyc: bounds_checked(values)
        break


class Failure(Exception, ReturnException):
    pass


@error_return(Failure)
def error_return_read(app: App) -> int32:
    return app.commands["inc"](10)  # tpyc: ok


def main():
    app = App()
    holder = Holder()
    # Fields, chained fields, and local aliases use the same invocation path.
    commands = app.commands
    print("fields", app.commands["inc"](1), app.callbacks[0](2), commands["inc"](3))  # tpyc: ok
    # Mutating the alias must change the callable held by the original field.
    commands["inc"] = lambda x: x + 2
    print("alias", app.commands["inc"](3))  # tpyc: ok
    commands["inc"] = inc
    # A void callback must still execute when used as a statement.
    app.notifications["run"](3)  # tpyc: ok
    print("chained", holder.app.commands["inc"](4), app.read())  # tpyc: ok
    print("nested", app.groups[0][0](5))  # tpyc: ok
    # The property returns shared storage, and indexed invocation calls its getter once.
    selected = app.selected
    print("property-alias-bound")
    selected[0] = lambda x: x + 3
    property_result = app.selected[0](12)  # tpyc: ok
    print("property", property_result, app.callbacks[0](12))
    app.callbacks[0] = inc
    generic = Generic[int32](inc)
    print("generic", generic.invoke(6))  # tpyc: ok

    # The key is evaluated once, before the argument; skipped operands stay skipped.
    events: list[int32] = []
    print("order", app.commands[key(events)](arg(events)), events)  # tpyc: ok
    print("guard", False and app.commands[key(events)](arg(events)) > 0, events)  # tpyc: ok

    # Mutating through the opaque callable must affect the original reference.
    values: list[int32] = []
    print("mutation", app.writers["touch"](values), values)  # tpyc: ok
    mutate_then_read(app, values)

    # Mutable-reference arguments need the same temporary storage as direct calls.
    print("literal-arg", app.writers["touch"]([1]))  # tpyc: ok
    print("repeat-arg", app.writers["touch"]([1] * 2))  # tpyc: ok
    print("comp-arg", app.writers["touch"]([1 for _ in range(2)]))  # tpyc: ok
    print("return-arg", app.writers["touch"](make_values()))  # tpyc: ok
    print("local-arg", commands["inc"](1), app.direct_writer([1]))  # tpyc: ok
    writers = app.writers
    print("computed-local-arg", writers["touch"]([1]))  # tpyc: ok

    # Temporary initialization stays inside the conditional operand.
    print("temp-skipped", False and app.writers["touch"]([arg(events)]) > 0, events)  # tpyc: ok
    print("temp-taken", True and app.writers["touch"]([arg(events)]) > 0, events)  # tpyc: ok

    print("comprehension", [app.callbacks[0](x) for x in range(3)])  # tpyc: ok
    for value in generate(app):
        print("generator", value)
    print("async", asyncio.run(async_read(app)))

    def nested() -> int32:
        return app.commands["inc"](6)  # tpyc: ok
    print("closure", nested())

    try:
        print("try", app.callbacks[0](7))  # tpyc: ok
    finally:
        print("finally", app.commands["inc"](8))  # tpyc: ok

    tag = app.commands["inc"](0)
    # Keep a non-exhaustive arm to exercise invocation with the normal match warning.
    match tag:  # tpyc: warning(/non-exhaustive match/)
        case 1:
            print("match", app.commands["inc"](9))  # tpyc: ok

    with open("callbacks.txt", "w") as stream:
        stream.write("ok")
        print("context", app.commands["inc"](10))  # tpyc: ok
    try:
        result = error_return_read(app)
        print("error-return", result)
    except Failure:
        print("error-return", "unexpected")


# Module scope uses a distinct global-variable representation.
global_app = App()
print("global", global_app.commands["inc"](11))  # tpyc: ok
main()
