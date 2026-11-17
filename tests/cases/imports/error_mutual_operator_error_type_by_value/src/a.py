from tpy import ReturnException, int32


class E(Exception, ReturnException):
    pass


import b


def run() -> int32:
    return b.use()
