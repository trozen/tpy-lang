# List repeat cannot be passed directly to Span -- assign to a variable first
from tpy import int32, Span

def takes_span(s: Span[int32]) -> None:
    for v in s:
        print(v)

def test_variable_count() -> None:
    n: int32 = 3
    takes_span([7] * n)  # tpyc: error(/Cannot pass list repeat directly/)
