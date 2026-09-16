"""Indexed callable fields retain normal argument and index diagnostics."""

import pytest

from ..diagnostics import SemanticError
from ..thir.testutil import _compile


SOURCE = '''from typing import Callable
from tpy import int32
def inc(x: int32) -> int32:
    return x + 1
class App:
    commands: dict[str, Callable[[int32], int32]]
    values: list[int32]
    callback: Callable[[int32], int32]
    def __init__(self):
        self.commands = {"inc": inc}
        self.values = [1]
        self.callback = inc
def main():
    app = App()
    SUBJECT
main()
'''


@pytest.mark.parametrize("subject,message", [
    ('app.commands["inc"]()', r"Callable type expects 1 argument\(s\), got 0"),
    ('app.commands["inc"]("bad")', r"expected int32, got str"),
    ('app.commands["inc"](x=1)', r"Keyword arguments are not supported for Callable"),
    ('app.commands[1](1)', r"expected str, got"),
    ('app.values[0](1)', r"Cannot call method '__call__' on type int32"),
    ('app.callback[:](1)', r"Slicing is not supported for Callable"),
    ('app.callback[::2](1)', r"Slicing is not supported for Callable"),
    ('app.callback[0, 1](1)', r"Subscript index must be an integer type"),
    ('callback = app.callback\n    callback[0](1)', r"Cannot index type Callable"),
    ('callback = app.callback\n    callback[:](1)', r"Slicing is not supported for Callable"),
])
def test_indexed_callable_field_errors(subject: str, message: str) -> None:
    with pytest.raises(SemanticError, match=message):
        _compile(SOURCE.replace("SUBJECT", subject))


RECEIVER_SOURCE = '''from typing import Callable
from tpy import int32, Own, nocopy
def inc(x: int32) -> int32:
    return x + 1
@nocopy
class App:
    callbacks: list[Callable[[int32], int32]]
    def __init__(self):
        self.callbacks = [inc]
def take(a: Own[App]) -> Own[App]:
    return a
def get(a: App) -> App:
    return a
def main():
    a = App()
    SUBJECT
main()
'''


@pytest.mark.parametrize("subject", [
    'get(a).callbacks[0](1)',
    'take(a).callbacks[0](1)',
    '(b := get(a)).callbacks[0](1)',
    '(b := take(a)).callbacks[0](1)',
])
def test_indexed_callable_receiver_once(subject: str) -> None:
    # Sema only: lowering is tracked by BUGS.md#call-rooted-container-field-index.
    _compile(RECEIVER_SOURCE.replace('SUBJECT', subject))


def test_indexed_callable_receiver_own_still_checks_later_uses() -> None:
    source = RECEIVER_SOURCE.replace(
        'SUBJECT', 'take(a).callbacks[0](1)\n    get(a)')
    with pytest.raises(SemanticError, match='used after this point'):
        _compile(source)


PROTOCOL_SOURCE = '''from typing import Callable, Protocol
from tpy import int32
class HasCallbacks(Protocol):
    callbacks: list[Callable[[int32], int32]]
def invoke[T: HasCallbacks](a: T) -> int32:
    SUBJECT
'''


def test_indexed_callable_protocol_field() -> None:
    _compile(PROTOCOL_SOURCE.replace('SUBJECT', 'return a.callbacks[0](1)'))


def test_indexed_callable_protocol_missing_method_diagnostic() -> None:
    with pytest.raises(SemanticError, match="has no method 'missing'"):
        _compile(PROTOCOL_SOURCE.replace('SUBJECT', 'return a.missing[int32](1)'))
