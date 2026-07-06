# A cross-module exposed enum used as a getset field is a located error (its
# CPython type handle lives in the defining module's glue); a local one is valid.
# tpy: ext_module
from tpy.extern import export
from enum_mod import Color


@export
class Holder:
    def __init__(self, c: Color):
        self.c = c  # tpyc: error(/is an exposed enum from another module/)
