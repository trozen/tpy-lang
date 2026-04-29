# Qualified exception class name in except clause:
# 'except pkg.ErrorClass:' should parse and codegen the same as the imported
# bare-name form. Covers a stdlib-bare-import case (`import re; except re.error`),
# a builtins-qualified case (`except builtins.ValueError`), an `as e:` binding
# with a qualified name, and a shadowing case where a local non-exception class
# shares the bare name of the qualified target. Bare-name regression also
# exercised.
import builtins
import re


class MyError(Exception):
    pass


# Local non-exception class shadowing 're.error' bare name. The qualified
# 'except re.error' below must still resolve to re.error, not this class.
class error:
    pass


def trigger_re() -> None:
    p = re.compile("(unbalanced")


def main() -> None:
    try:
        trigger_re()
    except re.error:
        print("re.error caught")

    # Binding form: 'as e' with a qualified name should bind.
    try:
        trigger_re()
    except re.error as e:
        print("bound")

    try:
        raise MyError()
    except MyError:
        print("MyError caught")

    try:
        raise ValueError()
    except builtins.ValueError:
        print("builtins.ValueError caught")


main()
