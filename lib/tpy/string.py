# string -- common string constants
# tpy: cpp_namespace("tpystd::string_mod")
from typing import Final

ascii_lowercase: Final[str] = "abcdefghijklmnopqrstuvwxyz"
ascii_uppercase: Final[str] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ascii_letters: Final[str] = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
digits: Final[str] = "0123456789"
hexdigits: Final[str] = "0123456789abcdefABCDEF"
octdigits: Final[str] = "01234567"
punctuation: Final[str] = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
whitespace: Final[str] = " \t\n\r\x0b\x0c"
printable: Final[str] = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~ \t\n\r\x0b\x0c"
