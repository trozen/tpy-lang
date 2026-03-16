# tpy: native_module
# TODO: add `# tpy: include <tpy/tpy.hpp>` once builtins are fully migrated,
# then drop the hardcoded #include <tpy/tpy.hpp> from codegen preamble
# TODO: add a header prefix directive (e.g. `# tpy: cpp_header_prefix tpy_rt`)
# to avoid generated "tpy/unsafe.hpp" clashing with runtime <tpy/tpy.hpp> paths
