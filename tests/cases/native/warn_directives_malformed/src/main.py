# Test warnings for malformed directive arguments
# tpyc: warning(/expects 1 positional/)
# tpy: include()
# tpyc: warning(/must be a str/)
# tpy: include(42)
# tpyc: warning(/unknown keyword/)
# tpy: link("x", bad_kw="y")
# tpyc: warning(/invalid namespace/)
# tpy: cpp_namespace("1bad-ns")

def main() -> None:
    pass

main()
