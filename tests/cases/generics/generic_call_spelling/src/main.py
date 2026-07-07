# Explicit template-arg spelling at generic call sites: a plain f[T] call
# with literal args (ref-slot temps) and with name args, a same-module
# generic static, and a module-qualified generic call.
import genmod


def pick[T](a: T, b: T) -> T:
    return b


class Util:
    @staticmethod
    def smax[T](a: T, b: T) -> T:
        return b


def main():
    p = pick(1, 2)
    x = 3
    y = 4
    print(p, pick(x, y))
    print(Util.smax(x, y))
    print(genmod.gf(x, y))


main()
