"""Callable spellings share the lambda-body argument admission."""

import pytest

from .nodes import THIRInlineTemp
from .testutil import _lower_fn


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
    # A lambda body has no statement to hold a temporary, and needs none: the
    # fresh list binds the written parameter as a temporary of the body's
    # own expression.
    fn = _lower_fn(source, "run")
    assert fn is not None
    assert _has_inline_temp(fn)


def _has_inline_temp(node: object) -> bool:
    if isinstance(node, THIRInlineTemp):
        return node.mutable
    fields = getattr(node, "__dataclass_fields__", None)
    children = ([getattr(node, f) for f in fields] if fields
                else node if isinstance(node, (list, tuple)) else ())
    return any(_has_inline_temp(c) for c in children)
