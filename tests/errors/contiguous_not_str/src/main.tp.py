"""Tests that str does NOT coerce to Span[Char] (str is not NativeContiguous)."""
from tpy import Char, Span

def takes_span(values: Span[Char]) -> None:
    pass

def main() -> None:
    s: str = "hello"
    takes_span(s)  # tpyc: error(/cannot|coerce|Span/i)

main()
