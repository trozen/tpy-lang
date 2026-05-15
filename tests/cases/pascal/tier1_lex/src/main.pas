{ Tier-1 lexer additions: hex literal $FF, control-char #nn embedded
  in (and concatenated with) string literals. }
program LexTest;
var
  n: integer;
  s: string;
begin
  n := $a0;
  writeln(n);                  { 160 }
  s := 'line1'#13#10'line2';   { two-line string }
  writeln(s);
end.
