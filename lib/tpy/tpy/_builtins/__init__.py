# tpy: native_module(forward=True)
from ._exceptions import BaseException, Exception, StopIteration
from ._range import Range, range
from ._funcs import len, repr, hash, chr, ord, abs, min, max, pow, divmod, next, iter, round, print, isinstance, all, any, sum, sorted, bin, hex, oct, enumerate, reversed, zip
from ._bytes import bytes, bytearray
from ._types import bool, int, float, str
from ._list import list
from ._dict import dict, dict_keys, dict_values, dict_items
from ._set import set
from ._io import TextIO, open
