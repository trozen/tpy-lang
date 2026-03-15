# Test that # tpy: directives after code produce a warning
def main() -> None:
    pass

# tpyc: warning(/before any code/)
# tpy: include("late.h")

main()
