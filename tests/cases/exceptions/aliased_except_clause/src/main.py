# `from X import error as MyErr; except MyErr:` previously codegened
# `catch (const MyErr& exc)` -- the alias name used as a C++ type, which
# fails to compile. Codegen now resolves the alias to the canonical
# record name and qualifies it cross-module.
from re import error as re_error, compile as re_compile

def main() -> None:
    try:
        re_compile("[invalid")
    except re_error:
        print("caught re_error")

    # `except <alias> as <name>:` exercises the bound-binding catch path,
    # which threads the same alias resolution.
    try:
        re_compile("(?P<>x)")
    except re_error as _exc:
        print("caught with binding")

main()
