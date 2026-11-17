# The star import of the suspended `abp.api` finds helper and use already
# bound here: CPython keeps them, as TPy does.
def helper() -> int:
    return 7


def use() -> int:
    return helper()


from .api import *  # noqa: E402  # tpyc: ok
