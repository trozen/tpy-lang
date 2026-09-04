# The adjacent qualified-call Optional slot a whole-optional pass-through does
# NOT cover: a tuple LITERAL there has to materialize a slot-typed temp, which
# is a different render from binding an existing optional binding bare.
import net


def call() -> int:
    return net.request("http://x", ("user", "pw"))  # tpyc: error(/qualcall.arg.optional/)


def main():
    print(call())


main()
