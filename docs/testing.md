# Testing discipline

The clock-sync work produced the same defect shape at four separate levels, and
the lessons below were each paid for by a bug that a green test suite did not
catch. They are written down so they survive the next refactor.

## A green suite is worth what its ability to go red is worth

Every non-trivial behaviour here is mutation-checked: break the thing on
purpose, confirm the guarding test fails, restore. Several tests passed
against code with the bug present until a mutation proved they could not fail:

- a concurrency test whose fake never yielded to the event loop, so no competing
  task could ever interleave and the BLE lock it "tested" was irrelevant;
- an outcome test that polled successfully before polling unsuccessfully, so the
  failure path it existed to cover was never reached — written *after* being
  told that exact path was untested, and repeating the mistake one level down;
- an entity test that never went through availability, missing that Home
  Assistant strips attributes from an unavailable entity;
- a bound (`CLOCK_KNOWN_BAD_CEILING`) that could not bind with the shipped
  constants, so deleting it changed nothing.

## A mutation report must distinguish red, green and NOT APPLIED

A mutation whose anchor text no longer exists has not been tested. It is not a
pass and it is not a failure — it is a claim that was never made. A refactor
orphaned twelve mutations at once here; they appeared in the summary alongside
genuine results, and only reading the per-line output showed they had never run.

Any tooling that reports mutation outcomes must therefore report three states,
and "not applied" must never be summarised as either of the other two. Re-anchor
it, or retire it deliberately — a retired mutation is honest, a stale one
pretending to be coverage is not.

## Model only what the evidence establishes; make unknowns permissive

Fakes here describe real hardware. Where its behaviour is unknown, the fake must
be *permissive* — it should let a bug through and fail the test, never absorb
it quietly.

The first version of `FakeInverter` cleared its staging registers on every arm
write and refused to commit a partial stage. Neither behaviour appears anywhere
in the captured evidence; both were invented. Together they made a real
corruption bug **inexpressible**, and every test passed with it present. A
missing test looks missing. A lying fake looks like coverage.

Two consequences worth keeping:

- Modelling the logger's response cache forced the fake's RTC to *tick*, because
  a frozen clock makes two honest consecutive reads identical to two cached
  ones. The fake got more truthful because the test needed it to be.
- Where a fake encodes an unconfirmed hypothesis (see `require_settle`), say so
  in the comment, and never let the fake be cited as evidence about the
  hardware.

## Write the test for the scenario that DISCRIMINATES

The recurring authoring error here is not forgetting a case — it is testing the
case you had in mind instead of the one that can tell the two implementations
apart. Two examples from the same afternoon, both caught by mutation rather than
by review:

- A sticky-flag test used a transport *error* for the later attempt. An
  exception skips the assignment entirely, so sticky and per-attempt behave
  identically and the test proved nothing. The discriminating case is an attempt
  that completes cleanly and merely fails to verify.
- A cancellation test cancelled *before* the shielded write began. That is
  survivable without the shield, because the preceding await already consumed
  the cancellation. Shielding only matters for a second cancellation arriving
  while the write is in flight.

Before writing the assertion, state what the broken implementation would do
differently in this exact scenario. If the answer is "nothing", the scenario is
wrong — not the assertion.

## A fault that lives in a GAP is better removed than guarded

The first critical defect found in this feature was a partial stage: one of the
three clock registers landing, an error interrupting the rest, and the commit's
falling edge latching a mixture of new date and stale time. Real defect, real
code, and it earned real defences — an always-ON flag policy, a sticky
known-wrong flag, a bounded correction budget.

The hazard existed only because the clock was written as three frames. Writing
it as one contiguous frame removes the gap the fault lived in, and with it the
entire class — there is no longer a moment between register writes for an error
to arrive. The two regression tests for it broke when the block write landed,
because the state they described can no longer be constructed.

Two things follow, and they pull in opposite directions:

- When a fault lives in the gap between operations, ask whether the gap is
  necessary before building machinery to survive it. We spent hours on defences
  for a hazard we were manufacturing.
- Do NOT delete those tests. Rework them to guard what remains (here: an
  interrupted write must leave the RTC untouched) and point them at the
  constraint that keeps the hazard gone. The property "the clock is written as
  ONE frame" is now load-bearing, and a test asserting it is what stops someone
  reintroducing the gap and the 80% corruption rate along with it.

## The verifier is not itself verified

Three times in this work, tooling reported an outcome it had not established: a
fake that could not express a bug, a patch script that printed success for a
replacement that never matched, and a mutation summary that folded
"not applied" into its results. Treat a summary line as a claim like any other.

## The recurring bug shape

Watch for **the thing that reports failure being unable to report failure**. In
this feature alone: the inverter's Time Sync flag silently disabling cloud
calibration; a swallowed re-enable error returning success; an outcome that
vanished exactly when a poll failed; a Jinja truthiness bug that would have kept
the failure alarm permanently silent; and a cached read-back capable of
approving a corrupted clock. When adding a check, ask what happens when the
check itself fails, and prefer a design that removes the failure mode over one
that handles it.
