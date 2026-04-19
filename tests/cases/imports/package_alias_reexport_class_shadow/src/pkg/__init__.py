# Local `class Foo` defined after `from .sub import Foo` must shadow the import
# in pkg's exports. Exercises the registry-side dedupe in
# compiler.py::_exports_to_module_info: without the local `class Foo`
# overwriting registry.records["Foo"], the imported version from .sub would
# leak out as pkg.Foo.
from .sub import Foo


class Foo:
    tag: str

    def __init__(self) -> None:
        self.tag = "from_init"
