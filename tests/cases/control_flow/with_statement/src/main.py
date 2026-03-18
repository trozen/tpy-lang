# Basic with statement: __enter__/__exit__ context manager protocol
from typing import Self


class Logger:
    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> Self:
        print(f"enter {self.name}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print(f"exit {self.name}")

    def log(self, msg: str) -> None:
        print(f"[{self.name}] {msg}")


class Connection:
    active: bool

    def __init__(self) -> None:
        self.active = True

    def __enter__(self) -> str:
        print("connecting")
        return "session-42"

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.active = False
        print("disconnected")


def test_basic() -> None:
    with Logger("A") as a:
        a.log("hello")
    print("after with")


def test_no_as() -> None:
    with Logger("B"):
        print("inside B")
    print("after B")


def test_enter_returns_different_type() -> None:
    with Connection() as session:
        print(session)
    print("after connection")


def test_multiple_ctx_managers() -> None:
    with Logger("X") as x, Logger("Y") as y:
        x.log("first")
        y.log("second")
    print("after both")


def test_variable_visible_after() -> None:
    with Logger("V") as v:
        v.log("inside")
    v.log("after")


def early_return_helper() -> str:
    with Logger("R") as r:
        r.log("before return")
        return "result"


def test_early_return() -> None:
    """__exit__ must fire even when returning from inside the with block."""
    val = early_return_helper()
    print(val)


test_basic()
print("---")
test_no_as()
print("---")
test_enter_returns_different_type()
print("---")
test_multiple_ctx_managers()
print("---")
test_variable_visible_after()
print("---")
test_early_return()
