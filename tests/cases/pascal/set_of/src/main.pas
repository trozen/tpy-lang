(* M12: full `set of T`. Tests set type declarations, literal
   construction (with and without range elements), the three set ops
   (`+` union, `-` difference, `*` intersection), and `in` membership
   on a set-typed variable. *)
program SetOf;
type
  IntSet = set of integer;
var
  a, b, c: IntSet;
  x: integer;
begin
  a := [1, 2, 3];
  b := [3, 4, 5];
  c := a + b;            (* union: 1..5 *)
  for x := 1 to 6 do
    if x in c then writeln(x);
  writeln('---');
  c := a * b;            (* intersection: 3 *)
  for x := 1 to 6 do
    if x in c then writeln(x);
  writeln('---');
  c := a - b;            (* difference: 1, 2 *)
  for x := 1 to 6 do
    if x in c then writeln(x);
  writeln('---');
  c := [1..3, 7];        (* range + scalar in one literal *)
  for x := 1 to 8 do
    if x in c then writeln(x);
end.
