# celld failover gate

The workers-runtime fleet promises two things: exactly one process serves
a cell at a time, and a write is not acknowledged until it survives a
failure. A fleet that is merely running proves neither, and this arc has
twice shipped something that looked healthy and was not connected to
anything. This directory is the gate for that slice: it measures whether
the fleet keeps the second promise while the first one is violated on
purpose.

The result is counts, not a verdict: how many writes the fleet
acknowledged, how many of those read back afterwards, and the gap. A gap
of zero is the promise kept in that run. Anything else is the finding.

## The measurement

A write is acknowledged when the client receives the probe's success
response. The runtime holds every write response until a durability proof
covers the write (the acknowledgement rule, RPO=0, in the celld
guarantees documentation), so a received success means the write survives
a failure. Everything short of that is an attempt, in two classes:

- `reject`: the fleet answered with an error status. Overload and drain
  answers are defined not to start the work, so the write is not
  acknowledged, though it may still be present.
- `unknown`: timeout, reset, or an answer that cannot be attributed to a
  sequence number. The write may or may not be durable. An absent
  acknowledgement does not prove a write is absent, so unknowns are
  reported but never counted as lost, and unknowns that landed are
  information rather than a failure.

Verification reads every acknowledged write back by sequence number and
compares the full value, which carries the run id, so recovery of exactly
this run's writes is what gets counted.

## Layout

- `probe/`: the probe application, one fixed cell behind three routes.
  Deployed as a bundle with `celld deploy`; it is not part of the fleet
  manifests.
- `ledger-write.py`: the acknowledgement ledger. Writes continuously, one
  outstanding write at a time, and fsyncs one ledger line per write
  before sending the next.
- `verify-ledger.py`: reads every acknowledged write back and reports
  acknowledged, recovered, and the gap.
- `ledger-stats.py`: what the acknowledgement stream did around a kill;
  evidence for attributing a failover, not a verdict.
- `stub-fleet.py` + `demo-stub.sh`: a local stub of the probe contract
  with two fault shapes, and a self-checking demonstration of the
  measurement.
- `scenario-1-kill-owner.sh`, `scenario-2-restart-all.sh`: the two
  disruptions. `lib.sh` holds their shared preflight; `resolve-owner.sh`
  is a best-effort owner lookup.

Ledgers and reports are captures: they stay uncommitted (see
`.gitignore`), and each run's numbers belong in the slice's notes.

## What it needs to run

- python3 (standard library only) and curl on the operator machine.
- The probe deployed to the fleet: `celld deploy probe/` with the bucket
  settings in the environment, exactly as the fleet's own configuration
  provides them. Deploying needs esbuild on PATH.
- `GATE_ENDPOINT`: the URL the probe is reachable at, through whatever
  ingress fronts the fleet. This repository is public, so endpoints stay
  parameters and nothing about network posture is written down here.
- For the scenario drivers only: kubectl access that can get, list,
  delete, and wait on pods in the fleet namespace, and exec for the
  optional owner lookup. `GATE_NAMESPACE`, `GATE_POD_SELECTOR`, and
  optionally `GATE_KUBE_CONTEXT` select the target.

No credential ever appears on a command line here. Bucket credentials
come from the operator's environment when deploying the probe; cluster
access comes from the operator's kubeconfig.

## Order of operations

The ledger comes first. Killing anything before the ledger is recording
produces the precise result this gate exists to avoid, a green-looking
answer with nothing behind it, so both drivers refuse to run unless the
ledger file is growing and fresh.

1. Deploy the probe (above).
2. Demonstrate the measurement on the healthy fleet: run
   `ledger-write.py` for several minutes with no disruption, then
   `verify-ledger.py`. Expect every write acked, recovered, gap 0. This
   is the fleet-side equivalent of `demo-stub.sh` and it must exist
   before any kill.
3. Keep the writer running. Run scenario 1.
4. Keep the writer running (restart it fresh if the ledger grew stale).
   Run scenario 2.

## Scenario 1: kill the process that owns a cell

Exercises the independence assumption: a write is durable because a peer
held it, and that protects the write only if the peer fails independently
of the owner. Two caveats are enforced by the driver, because each can
turn the run into a green-looking nothing:

- One process per machine is a correctness requirement, not a nicety. If
  two processes share a machine, one machine can take the owner and its
  peer copy at once, and a kill proves nothing. The driver checks pod
  placement and refuses unless `--force` asks for a knowingly
  inconclusive run.
- The kill is ungraceful. A graceful delete sends SIGTERM, and celld
  hands its cells off cleanly on SIGTERM: that exercises the handoff
  path, not the fence and takeover. `--graceful` runs that other path as
  a separate, labelled data point.

There is no documented cell-to-owner lookup, so the driver resolves the
owner three ways, in order: `--pod NAME` if the operator knows it, the
best-effort `resolve-owner.sh` (built on the alpha `/state` operator API,
unconfirmed until it runs against the live fleet), and otherwise a sweep
that kills each process in turn with recovery verified between kills,
guaranteeing the owner died exactly once. `ledger-stats.py` prints what
the acknowledgement stream did around each kill; a kill consistent with a
takeover (an acknowledgement pause, or writes gone unknown) is the owner
kill. If no kill shows one, the driver calls the run inconclusive even
when the gap is zero, because a zero gap without a demonstrated owner
kill is the fleet never having been tested.

## Scenario 2: restart every process at once

Exercises the assumption the disposable scratch stands on: a write is
durable because a peer holds it, not because a local disk does.
Force-deleting every process at the same moment wipes every local copy at
once, so any acknowledged write whose bucket upload had not completed has
nowhere left to live. This is the scenario the per-process storage
decision turns on, and it is easy to skip because scenario 1 looks like
the whole test. It is not: one dead owner leaves the other disks alive,
which is exactly what scenario 2 does not.

A rolling or graceful restart does not count. Letting each node seal and
hand off before the next goes is the one path that never tests this
assumption, which is why the driver force-deletes all pods in one call.

Either failure shape is a finding: acknowledged reads that come back
missing (loss), and reads that never come back because the recovery gate
holds the cell closed without follower witnesses (unavailability, which
the verifier reports as missing after its deadline; the run log shows the
difference). A zero gap with the cell serving again is the result
ephemeral scratch needs.

## The stub demonstration

`./demo-stub.sh` runs the measurement against `stub-fleet.py`, a local
stub of the probe contract, in three phases: a healthy run (expect gap
0), a hang window that mimics a takeover (expect gap 0 with unknowns that
landed), and five deleted acknowledged writes (expect gap 5, with the
missing sequence numbers named). The last phase matters most: a harness
that has never reported a loss cannot be trusted to stay silent only when
there is none. The demonstration validates the harness. It says nothing
about celld or the fleet, and it is not a substitute for step 2 above.

## Unknowns until a live fleet exists

- Whether `resolve-owner.sh`'s reading of `/state` matches the deployed
  release. The API is documented as alpha; the sweep path exists because
  of this.
- The acknowledgement pause a takeover actually produces here. The
  driver's takeover window defaults to 5 seconds (`GATE_TAKEOVER_S`);
  calibrate from the first healthy-fleet run's latencies and the
  configured lease lifetime.
- How the ingress behaves during a takeover: queued requests show as slow
  acks, refused ones as unknowns. Both are valid data; the mix is
  unknown until measured.
- Recovery duration under real data, which sets the verifier's
  `--deadline`. The runtime's own documentation says a large dead node
  can hold recovery open for minutes.
- The outcome of scenario 2 itself. No expected value is baked into the
  harness, because the answer is what the gate exists to produce.

## Why this lives in `gates/`

The object-storage slice before this one passed two gates and left no
convention for where such a harness lives. `gates/<slice>/` is the
proposal: one directory per platform-slice gate, versioned with the repo
it gates, and kept out of `k8s/`, which the GitOps reconciler owns. The
rio-artifacts convention would place this under a `.rio` workspace; this
repository has none, so the harness lives here instead.
