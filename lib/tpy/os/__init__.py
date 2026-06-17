# os -- miscellaneous operating system interfaces.
# tpy: cpp_namespace("tpystd::os")
#
# v1 stands up only the `os.path` submodule (pure-string path manipulation).
# The filesystem surface (getcwd/environ/stat/listdir/...) is not yet built.
from . import path
