# An exposed enum imported from another ext_module cannot cross as a container
# element (its CPython type handle lives in the defining module's glue) -- the
# enum sibling of error_export_container_cross_module_element.
# tpy: ext_module
from tpy import Int64
from tpy.extern import export
from enum_mod import Color


@export
def f(cs: list[Color]) -> Int64:  # tpyc: error(/exposed-enum container element from another module/)
    return len(cs)
