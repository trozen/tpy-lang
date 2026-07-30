# `except ... as cls` binds a name the same way an assignment does, so it is
# a rebind of the defining-class alias -- and unlike an assignment it is not an
# ast.Name node, which is how it used to slip through.
from tpy import Int32


class MyError(Exception):
    def __init__(self, code: Int32):
        self.code = code


class Registry:
    code: Int32 = 999

    @classmethod
    def run(cls) -> Int32:
        try:
            raise MyError(7)
        except MyError as cls:  # tpyc: error(/Cannot rebind 'cls'/)
            return cls.code


def main() -> None:
    print(Registry.run())


main()
