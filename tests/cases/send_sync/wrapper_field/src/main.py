# Send[Callable] in field position: the field emits as plain std::function
# (erasure in storage form), the record stays Send-derivable, and a
# Send-framed lambda constructs and calls through it.
from tpy import Int32, Send
from typing import Callable

class Handler:
    cb: Send[Callable[[Int32], None]]

    def __init__(self, cb: Send[Callable[[Int32], None]]) -> None:
        self.cb = cb

def run_handler(h: Handler) -> None:
    h.cb(1)

def main() -> None:
    h = Handler(lambda n: print("cb", n))
    run_handler(h)

main()
