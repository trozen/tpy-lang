{ Tier-1: succ/pred on integers + a deterministic random-range check.
  succ/pred are translator-side desugars routed to pascal.runtime.builtins;
  random's deterministic property is verified by running it without
  randomize and checking the value lands in the declared range. }
program SuccPredRandom;
var
  i, r: integer;
begin
  i := 5;
  writeln(succ(i));     { 6 }
  writeln(pred(i));     { 4 }
  r := random(100);
  if (r >= 0) and (r < 100) then writeln('in range')
  else writeln('out of range');
end.
