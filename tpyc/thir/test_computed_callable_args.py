"""Callable spellings share the existing lambda-body argument admission."""

import pytest

from .testutil import _strict_reject


@pytest.mark.parametrize("callee", [
    "cb", 'callbacks["run"]', "app.cb", 'app.callbacks["run"]',
])
def test_callable_temporary_arg_without_flush(callee: str) -> None:
    source = f'''from typing import Callable
from tpy import int32
def touch(values: list[int32]) -> int32:
    values.append(4)
    return len(values)
class App:
    cb: Callable[[list[int32]], int32]
    callbacks: dict[str, Callable[[list[int32]], int32]]
    def __init__(self):
        self.cb = touch
        self.callbacks = {{"run": touch}}
def run():
    app = App()
    cb: Callable[[list[int32]], int32] = touch
    callbacks: dict[str, Callable[[list[int32]], int32]] = {{"run": touch}}
    deferred: Callable[[], int32] = lambda: {callee}([1])
    print(deferred())
'''
    # Existing limitation: BUGS.md#lambda-body-reference-argument-temp.
    error, _ = _strict_reject(source)
    assert error.reason == "expr.lambda:lambda.body:expr.call:call.arg_shape.container"
