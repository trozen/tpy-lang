"""Tests that str -> Int32 coercion is rejected (no coercion path)."""
from tpy import Int32

def main() -> None:
    s: str = "hello"
    x: Int32 = s  # tpyc: error(/Type mismatch.*expected Int32, got str/)
