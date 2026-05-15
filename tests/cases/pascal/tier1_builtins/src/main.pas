{ Tier-1 built-ins: inc/dec (with and without step), sqr, odd, abs,
  chr, ord, plus char-typed variables and the implicit single-char
  string -> Char conversion at the assignment site. }
program BuiltinsTest;
var
  i: integer;
  c: char;
begin
  i := 5;
  inc(i);              writeln(i);  { 6 }
  inc(i, 3);           writeln(i);  { 9 }
  dec(i, 2);           writeln(i);  { 7 }
  writeln(sqr(i));                  { 49 }
  if odd(i) then writeln('odd') else writeln('even');
  writeln(abs(-42));                { 42 }
  c := 'A';
  writeln(ord(c));                  { 65 }
  writeln(chr(66));                 { B }
end.
