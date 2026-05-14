{ M12: subrange enforcement -- assigning an out-of-range value to a
  subrange-typed variable triggers the runtime `check_subrange` panic
  (TP-style range-check error). }
program PanicSubrange;
type
  Byte = 0..255;
var
  b: Byte;
begin
  b := 300;
  writeln(b);
end.
