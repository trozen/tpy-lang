# Regression: match/case must isolate protocol_narrowings and literal_facts
# between cases. `assert isinstance(x, P)` inside one case body populates
# protocol_narrowings in codegen; without a case-boundary save/restore, the
# narrowing leaks and affects for-loop dispatch in later cases.
from typing import Iterable
from tpy import Int32, Spannable, span


def iter_sum(items: Iterable[Int32]) -> Int32:
    total: Int32 = 0
    for y in items:
        total += y
    return total


def process(items: Spannable[Int32] | Iterable[Int32], tag: str) -> Int32:
    match tag:
        case "span_path":
            # Present so codegen emits the Spannable narrowing; not executed
            # in main() below to keep the test CPython-compatible (list does
            # not match the runtime_checkable Spannable protocol in CPython).
            assert isinstance(items, Spannable)
            s = span(items)
            total: Int32 = 0
            for x in s:
                total += x
            return total
        case "iter_path":
            # items is still the union here; narrow to Iterable to iterate.
            # This body is emitted after "span_path" in codegen, which is
            # where a leaked Spannable narrowing would manifest.
            if isinstance(items, Iterable):
                return iter_sum(items)
            return -1
        case _:
            return -2


def main() -> None:
    nums: list[Int32] = [10, 20, 30]
    print(process(nums, "iter_path"))
    print(process(nums, "other"))


main()
