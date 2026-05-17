# v1.5 M2: isinstance(opt_exc, X) works on Optional[BaseException] outside
# __exit__ too -- not just in context-manager bodies. The same machinery
# (BaseException is @dynamic-rooted via Throwable) lowers the check to
# dynamic_cast on the inner pointer.
#
# Covers two sema paths:
#   - `classify` (post-`is None` narrowing): isinstance after the
#     non-None guard hits the post-narrowed branch of _analyze_isinstance.
#   - `is_value_error` / `is_cancelled` (Optional-direct): isinstance on
#     the still-Optional value with no preceding `is None` guard hits the
#     Optional-direct branch -- distinct codegen path.

from typing import Optional
from tpy import CancelledError


def classify(e: Optional[BaseException]) -> str:
    if e is None:
        return "<none>"
    if isinstance(e, ValueError):  # tpyc: ok
        return "VE: " + str(e)
    if isinstance(e, (OSError, RuntimeError)):  # tpyc: ok
        return "OS/RE: " + str(e)
    if isinstance(e, CancelledError):  # tpyc: ok -- direct BaseException subclass
        return "CANCELLED"
    return "BASE: " + str(e)


def is_value_error(e: Optional[BaseException]) -> bool:
    # Optional-direct path: no preceding `is None` narrowing.
    return isinstance(e, ValueError)  # tpyc: ok


def main() -> None:
    print(classify(ValueError("v")))
    print(classify(RuntimeError("r")))
    print(classify(AssertionError("a")))
    print(classify(CancelledError("c")))
    print(classify(None))
    print(is_value_error(ValueError("v2")))
    print(is_value_error(RuntimeError("r2")))
    print(is_value_error(None))


main()
