"""Tests that str -> int32 coercion is rejected (no coercion path)."""
from tpy import int32

def main() -> None:
    s: str = "hello"
    x: int32 = s  # tpyc: error(/Type mismatch.*expected int32, got str/)
