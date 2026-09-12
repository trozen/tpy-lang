{ M18: procedural types -- `type Fn = procedure(x: integer);`
  declares a procedure-pointer type; values of that type can hold
  a named procedure and be called through. Maps to TPy `Callable[
  [int32], None]`. Function-typed callbacks (`function(...): R`)
  parse but the parameterless-bare-name rewrite suppresses
  function-reference assignment for now; this test exercises only
  the procedure form. }
program ProcTypes;
type
  IntAction = procedure(n: integer);
var
  action: IntAction;

procedure shout(n: integer);
begin
  writeln('shout: ', n);
end;

procedure whisper(n: integer);
begin
  writeln('whisper: ', n);
end;

begin
  action := shout;
  action(1);
  action := whisper;
  action(2);
end.
