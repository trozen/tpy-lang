"""Test that 'not' operator rejects non-boolean/numeric types."""

def main() -> None:
    s: str = "hello"
    result = not s  # tpyc: error(/Invalid operand type for 'not': str/)
