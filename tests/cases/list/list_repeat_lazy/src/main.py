# Test lazy list repeat with variable count -- stays as repeat_range
from tpy import int32
from typing import Iterable

def consume(items: Iterable[int32]) -> None:
    total: int32 = 0
    for v in items:
        total += v
    print(total)

def test_lazy_variable_count() -> None:
    n: int32 = 5
    x = [7] * n  # tpyc: type(/repeat\[/)
    print(len(x))
    consume(x)

def test_lazy_for_loop() -> None:
    n: int32 = 3
    r = [10] * n  # tpyc: type(/repeat\[/)
    for v in r:
        print(v)

def test_direct_iterable_arg() -> None:
    # Pass repeat literal directly to Iterable param
    consume([3] * 4)

def test_print_lazy_repeat() -> None:
    # Print on lazy repeat uses ListPrinter
    n: int32 = 4
    r = [5] * n  # tpyc: type(/repeat\[/)
    print(r)

test_lazy_variable_count()
test_lazy_for_loop()
test_direct_iterable_arg()
test_print_lazy_repeat()
