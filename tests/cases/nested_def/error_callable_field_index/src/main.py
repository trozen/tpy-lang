# App.callback holds the function inc itself, not a list or dict of functions.
# Calling app.callback(1) is valid. app.callback[0](1) first tries to retrieve
# element 0 from that function, which is invalid and must fail during indexing.
#
# Require a "Cannot index type Callable" error. The compiler must not mistake
# the brackets for generic call syntax and silently discard [0], turning the
# invalid expression into the valid call app.callback(1).
from typing import Callable
from tpy import int32


def inc(x: int32) -> int32:
    return x + 1


class App:
    callback: Callable[[int32], int32]

    def __init__(self):
        self.callback = inc


def main():
    app = App()
    # The brackets must be checked even though calling the field itself is valid.
    print(app.callback[0](1))  # tpyc: error(/Cannot index type Callable/)


main()
