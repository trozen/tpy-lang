# Pass a cross-module __span__-bearing value to a Span parameter WITHOUT
# importing its type (only make_pool). Span-coercion must resolve
# Pool[int32].__span__ -- it needs the record by qname, which is absent from
# this module's local records dict. Regressed to "expected Span[...], got
# Pool[int32]" before the qname-first lookup fix in get_span_return_type.
from tpy import int32, Span, readonly
from pool import make_pool


def total(xs: Span[readonly[int32]]) -> int32:
    acc: int32 = 0
    for x in xs:
        acc += x
    return acc


def main() -> None:
    p = make_pool([10, 20, 30])
    print(total(p))


main()
