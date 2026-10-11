"""Analysis-only MIR: never an emission input. Every compile that emits C++
runs it after codegen for the argument-alias warning (`tpyc.mir_diagnostics`);
the test harness and `--dump-mir` read its verdicts."""
