# urllib.parse percent-encode/decode -- quote/quote_plus, unquote/unquote_plus,
# urlencode, parse_qsl -- UTF-8 multibyte, safe-char overrides, degenerate escapes.
from urllib.parse import (
    quote, quote_plus, unquote, unquote_plus, urlencode, parse_qsl,
)


def main() -> None:
    print(quote("AZaz09-._~"))
    print(quote("/a b/c?x=1"))
    print(quote("/a b", safe=""))
    print(quote("\u00e9\u4e2d"))   # UTF-8 multibyte (e-acute, CJK) -> %XX per byte

    print(quote_plus("a b+c/d"))           # space -> '+', '+' and '/' encoded
    print(unquote("%2Fa%20b%C3%A9"))
    print(unquote_plus("a+b%2Bc"))
    print(unquote("100%"))                 # trailing '%' with no hex -> literal
    print(unquote("%zz"))                  # invalid hex -> literal

    # urlencode iterates in insertion order (TPy dicts are ordered like CPython).
    print(urlencode({"limit": "10", "name": "a b", "path": "x/y"}))
    print(urlencode({}))

    # blank values and no-'=' keys drop by default; kept with keep_blank_values.
    for k, v in parse_qsl("a=1&b=hello+world&c=&noeq&d=z%2Fw"):
        print(k + "=" + v)
    print("--keep-blank--")
    for k, v in parse_qsl("a=1&b=&c", True):
        print("[" + k + "]=[" + v + "]")


main()
