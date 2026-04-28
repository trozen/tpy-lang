# JSONDecodeError surface: msg, doc, pos, lineno, colno.
# CPython compatibility check via the cpy phase.
import json
from json import JSONDecodeError


def expect_error(s: str) -> None:
    try:
        v = json.loads(s)
        print("UNEXPECTED OK")
    except JSONDecodeError as e:
        # Print msg + computed line/col so the cpy phase can compare.
        # `doc` and `pos` are runtime-equal to the inputs; not printed
        # to keep the output portable across CPython versions that
        # word the error messages slightly differently.
        print(e.lineno, e.colno)


def main() -> None:
    # Truncated object
    expect_error('{"key": ')
    # Trailing data
    expect_error('{} extra')
    # Unterminated string
    expect_error('{"key": "value')
    # Multi-line input -- error reported with correct line/col
    expect_error('{\n  "key" 123\n}')
    # Empty string
    expect_error("")
    # Just whitespace
    expect_error("   ")


main()
