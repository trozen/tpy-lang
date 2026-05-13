# Regression: `import pkg.sub` followed by `pkg.sub.Cls.method()` and
# `pkg.sub.Cls.CONST` works. The dotted-module case walks two TpyFieldAccess
# layers to recover the module name, then looks up the class.
import pkg.sub


def main() -> None:
    w = pkg.sub.Widget.make(13)  # tpyc: ok
    print(w.value)
    print(pkg.sub.Widget.SIZE)  # tpyc: ok


main()
