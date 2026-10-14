# Test that `__tpy__` "includes" are emitted in the generated header
__tpy__ = {"includes": ["mylib/mylib.h",
                        "<system_header.h>"]}

def main() -> None:
    pass

main()
