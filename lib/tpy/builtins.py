# tpy: native_module(forward=True)
# tpy: cpp_namespace("tpystd::builtins")
from tpy._builtins import (
    BaseException, Exception, StopIteration,
    Range, range,
    len, repr, hash, chr, ord, abs, min, max, pow, divmod, next,
    bool, int, float, str,
    list, dict, dict_keys, dict_values, dict_items,
    set,
)
