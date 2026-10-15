from tpy import ReturnException, int32


class E(Exception, ReturnException):
    pass


import b  # tpyc: warning(/Import statement should be at the top of the file/)


def run() -> int32:
    return b.use()
