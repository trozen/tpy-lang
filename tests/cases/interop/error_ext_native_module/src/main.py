# An ext_module emits a generated CPython module; native_module is
# declaration-only and emits no code -- the combination is contradictory and
# rejected (it would otherwise produce a PyInit_-less, unimportable .so).
# tpyc: error(/cannot be both .* ext_module.* and .* native_module/)
# tpy: ext_module
# tpy: native_module
