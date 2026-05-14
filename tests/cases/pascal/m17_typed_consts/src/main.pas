{ M17: typed constants -- `const arr: array[1..N] of T = (...);`.
  Used for lookup tables in TP7 programs. The translator emits a
  `ListLit`-initialised VarDecl whose type annotation is
  `Array[T, N]` -- TPy's analyze-with-hint specialises that
  combination to an array aggregate-init at codegen time. Typed-
  record-consts (`const p: Point = (x: 1; y: 2);`) are deferred. }
program TypedConsts;
const
  Primes: array[1..5] of integer = (2, 3, 5, 7, 11);
  Squares: array[1..4] of integer = (1, 4, 9, 16);
var
  i: integer;
begin
  for i := 1 to 5 do
    writeln(Primes[i]);
  for i := 1 to 4 do
    writeln(Squares[i]);
end.
