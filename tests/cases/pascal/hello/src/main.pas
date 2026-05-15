{ M1 hello-world: the Pascal frontend plugin parses this, the translator
  emits IR, lowering produces a TpyModule importing pascal.runtime.io.writeln,
  and end-to-end the program prints "Hello, World!". }
program Hello;
begin
  writeln('Hello, World!');
end.
