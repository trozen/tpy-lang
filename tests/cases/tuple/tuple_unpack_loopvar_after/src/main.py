# Leaked for-loop tuple variable: a loop target read after the loop aliases
# the last container element (CPython leaks the last iteration's value). The
# read after the loop must compile (not be rejected) and stay a zero-copy view
# -- the binding aliases the live `pairs` container, not a per-iteration temp.
def main() -> None:
    pairs = [("aa", "bbbb"), ("cc", "dddd")]
    for k, v in pairs:
        print(k, v)
    print(k)
    print(v)


main()
