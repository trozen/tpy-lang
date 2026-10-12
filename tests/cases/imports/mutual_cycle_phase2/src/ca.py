# Const across a cycle, callee in the member analyzed LATER (cb).
from tpy import int32
import cb


# Only forwards to cb.count, which reads: `xs` is a const reference.
def forward(xs: list[int32]) -> int32:
    return cb.count(xs)


def one() -> int32:
    return 1
