# Its own namespace spells the last part `mod_`, not the default `mod__`.
__tpy__ = {"cpp_namespace": "tpyapp::pkg_ns::mod_"}
from pkg_ns.other import one  # a back-edge through the parent package

print("ns_override: pkg_ns.mod_ init")


class Thing:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x + one()
