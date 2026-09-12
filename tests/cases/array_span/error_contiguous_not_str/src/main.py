"""Tests that str does NOT coerce to Span[char] (str is not Spannable)."""
from tpy import char, Span

def takes_span(values: Span[char]) -> None:
    pass

def main() -> None:
    s: str = "hello"
    takes_span(s)  # tpyc: error(/cannot|coerce|Span/i)

main()
