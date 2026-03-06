# Constant-count list repeat cannot be passed directly to Span either
from tpy import Int32, Span

def takes_span(s: Span[Int32]) -> None:
    for v in s:
        print(v)

def main() -> None:
    takes_span([7] * 3)  # tpyc: error(/Cannot pass list repeat directly/)
