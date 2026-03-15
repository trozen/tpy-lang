# Test that unknown # tpy: directives produce a warning
# tpyc: warning(/unknown.*directive/)
# tpy: frobnicate("something")

def main() -> None:
    pass

main()
