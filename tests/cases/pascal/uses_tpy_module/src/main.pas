{ Pascal-uses-TPy: program imports a sibling .py module via `uses`.
  The `uses` clause is polyglot - bare name resolves to .pas first, then .py
  in the same directory. Tests star-import lowering of TPy modules. }
program UsesTpyModule;
uses helpers;
begin
  writeln(double(7));
  writeln(triple(5));
end.
