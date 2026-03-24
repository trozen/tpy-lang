# tpy: native_module
# tpy: cpp_namespace("tpystd::tpy")
# Extern decorator names are parser keywords handled by the parser directly.
# This file exists so that relative imports (from ._extern import native)
# resolve to a valid module. Sema skips these names via is_parser_keyword()
# after mapping the private path to the public parent ("tpy.extern").
