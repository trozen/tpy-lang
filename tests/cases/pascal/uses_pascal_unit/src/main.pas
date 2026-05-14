{ Pascal-uses-Pascal: program imports a sibling .pas unit (MyMath) via
  the `uses` clause; interface section declares signatures, implementation
  section provides bodies. Tests star-import lowering of Pascal units. }
program UsesPascalUnit;
uses MyMath;
begin
  writeln(square(5));
  writeln(cube(3));
end.
