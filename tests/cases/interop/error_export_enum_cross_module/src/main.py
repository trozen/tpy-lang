# An exposed enum imported from another ext_module is a located error, not a
# misbuild: its CPython type handle lives in the defining module's glue.
# tpy: ext_module
from tpy.extern import export
from enum_mod import Color


@export
def step(c: Color) -> Color:  # tpyc: error(/is an exposed enum from another module/)
    return c
