# List repeat cannot be passed directly to Span -- assign to a variable first
from tpy import Int32, Span

def takes_span(s: Span[Int32]) -> None:
    for v in s:
        print(v)

def test_variable_count() -> None:
    n: Int32 = 3
    takes_span([7] * n)  # tpyc: error(/Cannot pass list repeat directly/)
