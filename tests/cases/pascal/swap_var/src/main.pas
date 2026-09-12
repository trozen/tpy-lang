{ M4: procedure with var (by-reference) parameters. The translator
  lowers the params to Ptr[int32], wraps caller args in take_ptr, and
  rewrites body reads/writes to deref / unsafe_store. }
program SwapTest;
procedure swap(var a, b: integer);
var
  tmp: integer;
begin
  tmp := a;
  a := b;
  b := tmp;
end;
var
  x, y: integer;
begin
  x := 1;
  y := 2;
  swap(x, y);
  writeln(x);
  writeln(y);
end.
