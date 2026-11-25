/-
  Executable entry point for `structa-formal-oracle` (dev/CI JSONL differential conformance tool).
-/

import StructaFormal.Oracle

open Lean
open StructaFormal

def main : IO UInt32 := do
  let stdin ← IO.getStdin
  let stdout ← IO.getStdout
  let stderr ← IO.getStderr
  let mut exitCode : UInt32 := 0
  repeat do
    let line ← stdin.getLine
    if line.isEmpty then
      break
    let trimmed := line.trimAscii.toString
    if trimmed.isEmpty then
      continue
    match Json.parse trimmed with
    | .error err =>
      stderr.putStrLn s!"JSON parse error: {err}"
      exitCode := 1
      break
    | .ok req =>
      match evaluateOracleRequest req with
      | .error err =>
        stderr.putStrLn s!"Oracle evaluation error: {err}"
        exitCode := 1
        break
      | .ok resp =>
        stdout.putStrLn resp.compress
  stdout.flush
  return exitCode
