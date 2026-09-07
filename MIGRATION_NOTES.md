# Migration notes

Commands for a human to run. Nothing here is applied automatically: each step
either handles a private key or mutates cluster state, and both are decisions
rather than side effects.

## A first sync needs no step from this document, because `make bootstrap` takes it

**`make bootstrap` now depends on `make secrets`, which loads both hand-held
Secrets from 1Password before Argo installs anything** — `iam-keys` into
`yadgar` and `yadgar-dev-ca` into `cert-manager`. It is idempotent, so running
it against a cluster that already holds them changes nothing. The sections below
remain the authority on what those Secrets ARE and where they come from; they
are no longer a checklist to remember on a recreate.

**Why that changed.** On 2026-09-05 a recreate came back with neither Secret and
the two failures did not look alike. The CA announces itself — the preflight Job
refuses, names the Secret and prints the command. `iam-keys` does not: both
`iam` pods sit in `ContainerCreating` with the reason only in `kubectl describe
pod`, and the `iam` Application reports **Healthy** throughout. A step that is
invisible when skipped does not belong in a document.

**Mint `iam-keys` — "The identity encryption keys" below — before the first
sync,** if you are not using `make bootstrap`. Everything else a first sync needs
now generates itself (ADR-0517): the cache password and the broker account come
from `infra/bootstrap/`, the administrative bootstrap token does too (ledger
638, below), and every internal certificate comes from `infra/internal-tls/`.
Without `iam-keys` a fresh cluster reaches every pod Ready
**except `iam`**, which stays in `ContainerCreating` naming the file it cannot
find.

**That one step is a decision, not a gap.** Those keys are data-bearing rather
than machine-only: the deployment could mint them, and if it did, a cluster
whose Secret was lost but whose database survived would get an `iam` that starts
and cannot decrypt the rows it already has. A pod that refuses to boot and says
why is strictly better than a service that is broken while reporting healthy, so
the refusal is kept on purpose. See the comment at the bottom of
`infra/bootstrap/secrets.yaml`.

**Two more things a human still owns, neither of them blocking.** The EDGE root
CA is minted by hand and kept in 1Password — a decision under ADR-0490, because
clients outside the cluster trust it and their trust store outlives `kind delete
cluster`, while the internal root has no such relying party. Until it is loaded
the `tls` Application is Degraded and `gateway.yadgar.internal` does not serve;
nothing else waits on it. And a cluster rebuild is still a rebuild — see the
next-but-one section.

---

## Authenticating the shared cache and the shared broker (ledger 518)

**NOTHING TO RUN.** This section opened with four steps that minted
`valkey-password` and `nats-auth` by hand and loaded them into the cluster.
They are deleted rather than moved: `infra/bootstrap/` creates them on first
sync (ADR-0517, ledger 528), along with `nats-auth-gateway` — three generated
Secrets now, not two. An estate that already holds them keeps exactly
what it has — the bootstrap Job's Role grants `create` alone, so a Secret that
exists answers 409 and is never rewritten.

The ordering that used to matter here — Secrets, then `deploy`, then `gateway`
and `iam` — was a property of the images running during that rollout. It is
spent, and it is gone with the steps it belonged to.

What remains is the part a human still owns: proving the cache refuses a
stranger, and rotating a password later.

### Why this exists

Verified 2026-09-02: Valkey ran with no `--requirepass`, NATS with no
authentication of any kind, and `grep -rl NetworkPolicy deploy/` matched nothing.
Anything on the pod network could read and rewrite D74's token buckets and D18's
epoch counters, and could **inject** events into D25's audit outbox. None of it
was a decision — the record contains no `requirepass`, `nkey`, `NetworkPolicy` or
`mTLS`, so it was undeclared rather than accepted.

**A NetworkPolicy would not have fixed it here.** This cluster's CNI is kindnet,
which does not implement NetworkPolicy: the object is accepted, displayed, and
never evaluated. One ships anyway as a second layer for real clusters — see
`infra/network-policies/` — but the thing that actually closes the gap is a
password on each server, because a server enforces it on every CNI.

### How the passwords are made now

`infra/bootstrap/secrets.yaml`, at sync wave -12, before `valkey` and `nats` at
-10. Each password is 33 random bytes as base64, written through the API with
`stringData` so **no trailing newline** is stored — the property the deleted
steps carried a warning about, now a property of the mechanism instead. 33 bytes
rather than 32 so the base64 carries no `=` padding.

Neither password reaches 1Password, and that is the intended trade (ADR-0517):
nobody reads them, and losing one costs a rotation rather than data.

```bash
kubectl -n yadgar logs job/bootstrap-secrets
# valkey-password: created            <- first sync
# valkey-password: already exists, left untouched   <- every sync after it
```

### Prove the cache actually refuses a stranger

The check that matters, and the only one that cannot pass against the old
configuration:

```bash
kubectl -n yadgar run valkey-probe --rm -it --restart=Never \
  --image=valkey/valkey:9.1.1 -- \
  valkey-cli -h valkey -p 6379 ping
```

`NOAUTH Authentication required.` is the pass. `PONG` means the cache is still
open — **and note that this command exits 0 either way**: `valkey-cli` prints the
refusal and returns success, which is why the readiness probe in
`infra/valkey/valkey.yaml` greps for `PONG` rather than trusting the exit code.
Read the output, do not script the exit status.

### To rotate a password later

**Still a human step, deliberately.** The bootstrap can create a Secret and
cannot update one, which is what makes a resync safe; the same limit means it
cannot rotate one either. A rotation is one `kubectl create secret
--dry-run=client -o yaml | kubectl apply -f -` and then rolling **both**
Deployments. The next sync sees the Secret present, answers 409, and leaves the
new password alone.

There is no staged window: `requirepass` holds one value, so the cache and the
gateway are briefly out of step however it is done. Roll the cache first — the
gateway then sees `NOAUTH`, refuses with `503` until it restarts, and a mounted
Secret does not reach a running process anyway.

---

## Recreate the kind cluster on the new port mapping (ledger 454)

**Do this first** — the TLS edge below cannot be reached until it is done.

`modules/nixos/kind.nix` in the nix repo now maps host 18080/18443 to node ports
**30080/30443** instead of 80/443. `extraPortMappings` is fixed at cluster
creation, so the running cluster still carries the old mapping and the change
takes effect only on a rebuild.

Why the mapping changed: Envoy Gateway provisions its own Envoy data plane and
its `EnvoyProxy` CRD exposes no `hostPort` or `hostNetwork` field, so nothing can
bind the node's `:443`. NodePort is the remaining route in and its default range
starts at 30000. `type: LoadBalancer` is not an alternative — under rootless
podman the host cannot route to container IPs at all (measured: the node sits on
`10.89.4.3` and the host cannot reach it), so every LB provider allocates
addresses on a network this machine cannot see.

```bash
sudo nixos-rebuild switch          # picks up the new kind.nix

KIND_EXPERIMENTAL_PROVIDER=podman kind delete cluster --name yadgar
sudo systemctl restart kind-yadgar-cluster
kubectl get nodes                  # 3 nodes Ready, on the new mapping
```

Everything in the cluster is GitOps, so Argo rebuilds it — and since ADR-0517
that now includes the cache password, both broker accounts and every internal
certificate. Three things still do not come back on their own:

- **the Argo CD install itself**: `make bootstrap` (needs `GITHUB_TOKEN`)
- **the EDGE CA secret**: step 3 of "The development TLS edge" below, from
  1Password
- **`iam-keys`**: "To restore them into a rebuilt cluster" below, from
  1Password, and **before** the first sync. `iam` will not start without it,
  and it is not generated on purpose — a fresh pair against a database that
  survived the rebuild would decrypt none of it.

The second one is the external-CA decision paying for itself, and it is the
reason the INTERNAL root is the opposite decision. A root minted inside the
cluster by a SelfSigned issuer is regenerated here — fatal for the edge, whose
certificate would quietly stop chaining to the root this host trusts, and
harmless for `infra/internal-tls/`, whose only relying parties are pods that
read the new bundle out of Secrets regenerated at the same moment.

---

## The identity encryption keys (ledger 452)

`iam` encrypts stored names with AES-256-GCM and looks usernames up by an
HMAC-SHA256 blind index. It refuses to boot without both keys.

**Losing the encryption key is unrecoverable.** Every stored name becomes
permanently unreadable — not degraded, gone. Losing the blind-index key is nearly
as bad: no login can find its user again, because the index it computes no longer
matches the ones in the table.

**THIS ONE IS STILL MINTED BY HAND, AND THAT IS THE DECISION.** `valkey-password`,
`nats-auth` and `nats-auth-gateway` are generated by `infra/bootstrap/` under
ADR-0517; these keys deliberately are not. ADR-0517 splits credentials into machine-only, where losing
one costs a rotation, and human-facing, which must be retrievable once. **These
are a third kind — data-bearing — and for that kind the rule inverts.**

Generate them automatically and a cluster whose Secret was lost but whose
database survived gets an `iam` that starts and cannot decrypt the rows it
already has: broken while reporting healthy. Leave them out of the bootstrap and
the same cluster gets a pod that refuses to start and names the missing file.
**The refusal is the feature.** It is also the only signal that the keys were
lost at all.

So a fresh cluster costs this one step, and it is the only one a first sync
needs. Run **once** per set of keys, and keep the same keys across cluster
rebuilds.

### 1. Mint both keys

```bash
cd "$(mktemp -d)"
umask 077
openssl rand -out encryption.key 32
openssl rand -out blind-index.key 32
```

32 bytes of raw material each — not base64, not a passphrase. `iam` refuses a key
of any other length rather than padding or truncating it into something that
silently does not match what encrypted the existing rows.

**Two keys, not one, and not the same key twice.** They are separate so that
compromising the lookup path does not also decrypt the data behind it.

### 2. Store them in 1Password

Raw bytes, so these go in as documents rather than text fields:

```bash
op document create encryption.key  --title "yadgar iam — encryption key"  --vault Private
op document create blind-index.key --title "yadgar iam — blind index key" --vault Private
```

1Password first, cluster second, so a cluster rebuild does not destroy them.

### 3. Load them into the cluster

Before the first sync, so `iam` never waits:

```bash
kubectl create secret generic iam-keys \
  --namespace yadgar \
  --from-file=encryption.key \
  --from-file=blind-index.key
```

Then destroy the local copies:

```bash
shred -u encryption.key blind-index.key
cd - && rmdir "$OLDPWD" 2>/dev/null || true
```

### To restore them into a rebuilt cluster

**`make secrets` does this, and `make bootstrap` depends on it.** What follows is
the same commands by hand, kept because they are the authority on which 1Password
items are read. Do it **before** the first sync of the rebuilt cluster:

```bash
op document get "yadgar iam — encryption key"  --out-file encryption.key
op document get "yadgar iam — blind index key" --out-file blind-index.key

kubectl create secret generic iam-keys \
  --namespace yadgar \
  --from-file=encryption.key \
  --from-file=blind-index.key

shred -u encryption.key blind-index.key
```

### If a cluster already holds keys that were never backed up

Copy them out before anything else destroys them:

```bash
cd "$(mktemp -d)"
umask 077
kubectl -n yadgar get secret iam-keys \
  -o jsonpath='{.data.encryption\.key}' | base64 -d > encryption.key
kubectl -n yadgar get secret iam-keys \
  -o jsonpath='{.data.blind-index\.key}' | base64 -d > blind-index.key
```

Then step 2 above, and `shred -u` both files.

### Check it took

```bash
kubectl -n yadgar get secret iam-keys -o jsonpath='{.data}' | grep -o 'encryption.key'
kubectl -n yadgar logs deploy/iam | grep 'crypto keys loaded'
```

A pod that cannot read them does not start and says why — that is D69's rule
applied to key material, and it is deliberate: a service that cannot decrypt what
it stored is broken rather than degraded.

---

## The internal service certificates (ledger 522)

**NOTHING TO RUN, EVER.** `infra/internal-tls/` mints a root on first sync with
a self-signed cert-manager Issuer, then issues one serving certificate per
service from it — `iam-tls`, `iam-db-tls`, `task-tls`, `task-db-tls`, in the
`yadgar` namespace, with the names the charts already default to. cert-manager
renews them without being asked, which is why they are its job rather than the
bootstrap Job's.

**This is not the edge, and the two are deliberately different.** The edge root
is minted by hand and lives in 1Password because clients OUTSIDE the cluster
trust it and their trust store outlives the cluster (ADR-0490). The internal
root's only relying parties are pods, each reading the bundle out of a Secret
that is regenerated with the cluster, so there is nothing to go stale and no
reason for a human to hold it.

**Nothing is encrypted yet.** Every chart's `tls.enabled` is still `false`. This
is the material the cut-over needs, not the cut-over.

```bash
kubectl -n yadgar get certificate
# each READY=True

kubectl -n yadgar get secret iam-tls -o jsonpath='{.data.tls\.crt}' \
  | base64 -d | openssl x509 -noout -text | grep -A1 'Subject Alternative Name'
# DNS:iam, DNS:iam.yadgar, DNS:iam.yadgar.svc, DNS:iam.yadgar.svc.cluster.local
```

**The SAN is the Service name and there is no IP SAN.** A client verifies
against the host it asked for rather than the address it reached, which is what
lets one certificate cover every pod behind a headless Service. Do not add an IP
SAN to make a by-IP probe work; make the probe use the name.

### The renewal residual, for whoever performs the cut-over

Certificates are read once at boot, so a renewed Secret does not reach a running
process. With `duration: 2160h` and `renewBefore: 720h` a pod must restart within
90 days of its certificate being issued or it serves an expired one. It blocks
nothing while `tls.enabled` is `false`, and it is the cut-over's to solve —
either a rollout triggered by the Secret's revision, or an accepted maximum pod
age.

---

## Issue `project-db` its serving certificate (ledger 641)

**`project-db` is deployed and crash-looping because no certificate authority
was ever told to issue it one.** Both pods refuse to boot with:

```
the TLS certificate at /var/run/config/serving-cert/tls.crt could not be read:
No such file or directory (os error 2). TLS was asked for, so this module
refuses to start rather than serving in cleartext.
```

That refusal is the module's fail-closed guard working exactly as designed. The
image pin is correct and the chart is correct. What is missing is the Secret:
`yadgarhq/argocd` sets `tls.enabled: true` for every module chart, the chart
therefore mounts a volume for `tls.certSecret`, whose default is
`project-db-tls`, and `optional: true` turns the absent Secret into an empty
directory rather than a `FailedMount` — so the binary reports the path instead
of kubelet reporting the volume. `infra/internal-tls/certificates.yaml` declared
`iam-tls`, `iam-db-tls`, `task-tls` and `task-db-tls` and no `project-db-tls`,
because the other four modules were onboarded before `project-db` existed.

**This is a GitOps change and Argo applies it.** `infra/internal-tls/` is synced
by the `deploy` Application, so merging the new `Certificate` is the whole of
the fix. The steps below CHECK it; only the last one is optional and only
because it shortens a wait.

### 1. Watch the certificate get issued

```bash
kubectl -n yadgar get certificate project-db-tls -w
# READY=True, normally within seconds of the sync
```

If it does not go Ready, read the reason rather than re-applying:

```bash
kubectl -n yadgar describe certificate project-db-tls
kubectl -n yadgar get certificaterequest | grep project-db
```

### 2. Confirm the Secret carries the three keys the two sides read

```bash
kubectl -n yadgar get secret project-db-tls \
  -o go-template='{{range $k,$v := .data}}{{$k}}{{"\n"}}{{end}}'
# ca.crt
# tls.crt
# tls.key
```

The pod selects `tls.crt` and `tls.key` and never `ca.crt`; a future CALLER of
`project-db` selects `ca.crt` out of this same Secret. Do not print the values.

### 3. Confirm the SANs are the Service name and no IP

```bash
kubectl -n yadgar get secret project-db-tls -o jsonpath='{.data.tls\.crt}' \
  | base64 -d | openssl x509 -noout -text \
  | grep -A1 'Subject Alternative Name'
# DNS:project-db, DNS:project-db.yadgar, DNS:project-db.yadgar.svc,
# DNS:project-db.yadgar.svc.cluster.local
```

```bash
kubectl -n yadgar get secret project-db-tls -o jsonpath='{.data.tls\.crt}' \
  | base64 -d | openssl x509 -noout -text | grep -A1 'Extended Key Usage'
# TLS Web Server Authentication   <- and NOT TLS Web Client Authentication
```

**The second command is the one worth running even when the pods come up.** One
authority signs both the serving and the client leaves in this namespace, and
the extended key usage is the only thing keeping the two directions apart. A
leaf naming both, or naming neither, is accepted by every server here and by
every client.

### 4. Confirm the pods go Ready, and that they are serving TLS

The pods are in `CrashLoopBackOff`, so kubelet picks the Secret up on its next
restart with no action from you — up to about five minutes of backoff. A rollout
restart only makes it prompt:

```bash
kubectl -n yadgar rollout restart deployment/project-db      # optional
kubectl -n yadgar rollout status deployment/project-db --timeout=300s
kubectl -n yadgar get pods -l app=project-db
# 2 pods, 1/1 Running
```

```bash
kubectl -n yadgar logs deployment/project-db | grep listening
# "project-db listening" with tls=true and a non-zero watching count
```

**Check the `tls` field rather than the restart count.** `tls=false` would mean
the flag never reached the binary, and a MISSING `tls` field would mean the
image predates the feature — different faults with different fixes (D81). A
non-zero `watching` is what says the rotation watcher of ADR-0523 holds this
certificate, so a renewal 58 days from now actually reaches the process.

### 5. Confirm the renewal instant did not collide — THIS ONE IS NOT OPTIONAL

```bash
kubectl -n yadgar get certificate \
  -o custom-columns='NAME:.metadata.name,RENEWAL:.status.renewalTime' \
  --sort-by=.status.renewalTime
```

**Every pair of adjacent renewal instants must be more than 300 seconds apart.**
That is the invariant the `renewBefore` ladder in
`infra/internal-tls/certificates.yaml` exists to buy: each service's pods draw a
rotation splay from `[0, 300)` independently, so two services sharing an instant
put all of their pods through a restart inside one five-minute window, and a
PodDisruptionBudget cannot hold that because a pod that exits 0 was never
evicted.

**`project-db-tls` is the first leaf that cannot inherit that separation from
its step, which is why this check is a step rather than a note.** The other
eight leaves were issued in one bootstrap and share a `notAfter`, so a distinct
`renewBefore` is a distinct instant. This one is minted on its own day:
`2160h - 768h` is `1392h`, which is exactly 232 six-hour steps, so its instant
inherits the hour-modulo-six, minute and second of ISSUANCE. Seven of the eight
existing instants sit on an `HH:40:52` grid at 6-hour spacing, and the window is
600 seconds wide rather than 300 because the new instant is too close on EITHER
side — so a collision is `600/21600`, ROUGHLY ONE IN THIRTY-SIX. Unlikely, and
nothing like impossible. The eighth instant is the edge leaf, which Envoy
Gateway reloads through xDS without restarting a pod, so it is not a target.

**If it did collide,** do NOT retune `renewBefore` to dodge it: any alignment
computed from today's `notAfter` dissolves at the first renewal. Delete the
Secret and let cert-manager re-issue at a different second instead:

```bash
kubectl -n yadgar delete secret project-db-tls
# cert-manager re-issues within a reconcile; then re-run the command above
```

That re-issues the leaf and rolls the two `project-db` pods a second time. It
touches no other service.

### What this deliberately does NOT add

**No client certificate for `project-db`.** `client-certificates.yaml` issues
one leaf per CALLER, and a `-db` module answers without calling —
`iam-db` and `task-db` have none either. Nothing in the estate dials
`project-db` over gRPC yet. Its one outbound connection is to its own MariaDB
engine, which is encrypted by `require_secure_transport` and authenticated by
the generated `project-db-password`, with `database.sslMode: required` checking
no certificate in either direction. The `project-db-mariadb-client-cert` Secret
in this namespace belongs to the MariaDB operator's own per-instance CA and is
unrelated to `yadgar-internal-ca`. When the `project` module is written and
dials `project-db`, THAT caller needs a leaf — `project-db` still will not.

---

## Issue `project` its two certificates, BEFORE the module exists (ledger 641)

**`yadgarhq/project` does not exist yet, and that is why this section is here
rather than later.** The certificates land first, on purpose.

`project-db` deployed and crash-looped because no authority had been told to
issue it a leaf. `yadgarhq/argocd` sets `tls.enabled: true` for every module
chart, the chart mounts a volume for `tls.certSecret`, and `optional: true`
turns an ABSENT Secret into an EMPTY DIRECTORY rather than a `FailedMount` — so
the binary reports a missing path instead of kubelet reporting a missing volume.
The refusal is the fail-closed guard working; the diagnosis is what was
expensive. `project` hits the identical wall for the identical reason.

**So the order is: these two certificates merge, THEN `yadgarhq/project` gets
its `yadgar-deployable` topic.** Nothing enforces that ordering. It is written
down because the last time it was discovered rather than followed.

Two objects, not one, because `project` both ANSWERS and CALLS:

| object               | file                                          | direction                      | `renewBefore` |
| -------------------- | --------------------------------------------- | ------------------------------ | ------------- |
| `project-tls`        | `infra/internal-tls/certificates.yaml`        | serving — `gateway` dials it   | `774h`        |
| `project-client-tls` | `infra/internal-tls/client-certificates.yaml` | client — it dials `project-db` | `780h`        |

`project-db` still gets no client leaf: it answers and never calls, the same
rule `iam-db` and `task-db` sit under.

**This is a GitOps change and Argo applies it.** `infra/internal-tls/` is synced
by the `deploy` Application, so merging the two `Certificate` objects is the
whole of the fix. The steps below CHECK it.

### 1. Watch both certificates get issued

```bash
kubectl -n yadgar get certificate project-tls project-client-tls -w
# READY=True for both, normally within seconds of the sync
```

If either does not go Ready, read the reason rather than re-applying:

```bash
kubectl -n yadgar describe certificate project-tls
kubectl -n yadgar get certificaterequest | grep project
```

### 2. Confirm each Secret carries the three keys the two sides read

```bash
kubectl -n yadgar get secret project-tls \
  -o go-template='{{range $k,$v := .data}}{{$k}}{{"\n"}}{{end}}'
# ca.crt
# tls.crt
# tls.key
```

```bash
kubectl -n yadgar get secret project-client-tls \
  -o go-template='{{range $k,$v := .data}}{{$k}}{{"\n"}}{{end}}'
# ca.crt
# tls.crt
# tls.key
```

`project`'s pod will mount BOTH: `tls.crt` and `tls.key` out of `project-tls` to
serve with, `tls.crt` and `tls.key` out of `project-client-tls` to call
`project-db` with, and `ca.crt` out of `project-db-tls` to verify `project-db`.
Do not print the values.

### 3. Confirm the SANs, and confirm the extended key usages point OPPOSITE ways

```bash
kubectl -n yadgar get secret project-tls -o jsonpath='{.data.tls\.crt}' \
  | base64 -d | openssl x509 -noout -text \
  | grep -A1 'Subject Alternative Name'
# DNS:project, DNS:project.yadgar, DNS:project.yadgar.svc,
# DNS:project.yadgar.svc.cluster.local
```

```bash
kubectl -n yadgar get secret project-client-tls -o jsonpath='{.data.tls\.crt}' \
  | base64 -d | openssl x509 -noout -text \
  | grep -A1 'Subject Alternative Name'
# DNS:project-caller     <- and NOT DNS:project
```

```bash
kubectl -n yadgar get secret project-tls -o jsonpath='{.data.tls\.crt}' \
  | base64 -d | openssl x509 -noout -text | grep -A1 'Extended Key Usage'
# TLS Web Server Authentication   <- and NOT TLS Web Client Authentication

kubectl -n yadgar get secret project-client-tls -o jsonpath='{.data.tls\.crt}' \
  | base64 -d | openssl x509 -noout -text | grep -A1 'Extended Key Usage'
# TLS Web Client Authentication   <- and NOT TLS Web Server Authentication
```

**The last two commands are the ones worth running even when everything comes
up.** One authority signs both directions in this namespace, and the extended
key usage is the only thing keeping them apart. A leaf naming both, or naming
neither, is accepted by every server here and by every client. `project` is the
first module in this file to hold BOTH kinds, so it is the first place a
copy-paste between the two objects would go unnoticed.

**And `project-caller` must not resolve.** `kubectl -n yadgar get svc
project-caller` must return `NotFound`, now and forever. The identity is spelled
that way precisely so an omitted `usages` block cannot yield a valid serving
certificate for a host something actually dials.

### 4. Pods — DEFERRED, and there is nothing to run yet

**There are no `project` pods.** `yadgarhq/project` is unwritten, so the
equivalent of `project-db`'s "confirm the pods go Ready" step cannot be run when
this merges. Run it at step 7 of the onboarding sequence instead, once the
module is tagged and the `yadgar-deployable` topic is added:

```bash
kubectl -n yadgar logs deployment/project | grep listening
# "project listening" with tls=true and a non-zero watching count
```

**That line is not the acceptance test.** It describes `project`'s own listener
and says nothing about the hop below it. The dial to `project-db` is lazy, so a
`project` whose `projectDb.tls` was never set comes up `1/1 Running`, reads Argo
`Synced`/`Healthy`, and fails every RPC. Drive a real `ResolveProject` or
`ListProjects` through it and read the answer.

**The client certificate has to join the rotation watch set (ADR-0523).** It is
read once from a directory mount that rotates in place, which is the whole test.
Forgetting it is worse than forgetting a serving leaf: an expired client
certificate does not weaken the hop, it stops it, ninety days after issuance and
thirty days after the replacement was already on disk.

### 5. Confirm the renewal instants did not collide — THIS ONE IS NOT OPTIONAL

```bash
kubectl -n yadgar get certificate \
  -o custom-columns='NAME:.metadata.name,RENEWAL:.status.renewalTime' \
  --sort-by=.status.renewalTime
```

**Every pair of adjacent renewal instants must be more than 300 seconds apart.**
That is the invariant the `renewBefore` ladder in
`infra/internal-tls/certificates.yaml` exists to buy: each service's pods draw a
rotation splay from `[0, 300)` independently, so two services sharing an instant
put all of their pods through a restart inside one five-minute window, and a
PodDisruptionBudget cannot hold that because a pod that exits 0 was never
evicted.

**These two leaves are minted on their own day, so their steps do not set their
instants.** `2160h - 774h = 1386h`, which is 231 six-hour steps exactly, and
`2160h - 780h = 1380h`, which is 230 — so each instant inherits the
hour-modulo-six, minute and second OF ISSUANCE rather than a ladder position.

**They share ONE draw, and land 6h apart from each other.** Both are minted in
the same reconcile, so they occupy the same offset in the six-hour cycle and are
separated by exactly one ladder step. They cannot collide with each other. One
unlucky draw puts BOTH beside an existing instant, so the remedy below applies
to both.

**THE ODDS ARE ONE IN EIGHTEEN, NOT THE ONE IN THIRTY-SIX THIS FILE QUOTES FOR
`project-db-tls`.** That figure assumed a single lane. Measured on 2026-09-07,
the live instants sit on TWO pod-restarting offsets: `0:40:52` for the seven
bootstrap leaves, and `4:28:33` for `project-db-tls`, which did not join the
grid. Two disjoint 600-second windows in a 21600-second cycle is `1200/21600`,
about 5.6%. The edge leaf's `0:46:04` is not a third lane — Envoy Gateway
reloads it through xDS and no pod restarts on it.

That figure ASSUMES the issuance second is uniform over the six-hour cycle, and
nothing measured says it is — cert-manager issues when an Argo reconcile reaches
the object. The two windows being DISJOINT is measured (the offsets are 2h12m19s
apart, far wider than 600 seconds), so the arithmetic follows from the premise;
the premise is the untested part. Run the check below rather than reasoning from
the odds — it is the check, not the figure, that tells you whether this leaf
collided.

**If either did collide,** do NOT retune `renewBefore` to dodge it: any
alignment computed from today's `notAfter` dissolves at the first renewal.
Delete the Secret and let cert-manager re-issue at a different second:

```bash
kubectl -n yadgar delete secret project-tls          # and/or project-client-tls
# cert-manager re-issues within a reconcile; then re-run the command above
```

Re-issuing before `project` is deployed costs nothing at all, which is another
reason to merge these certificates ahead of the module rather than beside it.

### What this deliberately does NOT add

**No client leaf for `project-db`.** It answers and never calls;
`iam-db` and `task-db` have none either. Its one outbound connection is to its
own MariaDB engine, which is encrypted by `require_secure_transport` and
authenticated by the generated `project-db-password`, under the MariaDB
operator's own per-instance CA rather than `yadgar-internal-ca`.

**No new leaf for the gateway.** `gateway-client-tls` already carries the
gateway's identity to `iam` and `task`; a third upstream reuses it. Client
leaves are per CALLER, not per hop.

**Nothing in `yadgarhq/argocd`.** The `projectDb: {tls: {enabled: true}}` key
that makes `project` verify `project-db`, and the `project: {tls: {enabled:
true}}` key that makes the GATEWAY verify `project`, are separate changes in a
separate repository. Merging these certificates does not set either.

---

## The development TLS edge (ledger 454)

Establishes HTTPS in front of the gateway so an MCP client on this machine
reaches it the way a real client would, over a certificate that verifies
properly rather than one anything has been told to ignore.

Run **once**. Everything after it arrives through git.

### Why a root CA at all, and why it lives outside the cluster

Let's Encrypt cannot issue here: HTTP-01 needs Let's Encrypt's servers to reach
this machine on public port 80, and DNS-01 would mean pointing a real domain and
its API credentials at a laptop. Both buy public PKI for traffic that never
leaves the host.

So the trust anchor is local. It is generated **here**, not in the cluster,
because a CA that cert-manager mints for itself is regenerated whenever the
cluster is rebuilt — and then the root NixOS trusts silently stops matching the
certificate the gateway serves. That failure looks like a TLS error of unclear
origin, and the natural next move is disabling verification, which deletes the
thing this exists to exercise.

### 1. Mint the root CA

```bash
cd "$(mktemp -d)"

openssl genrsa -out yadgar-dev-ca.key 4096

openssl req -x509 -new -nodes -key yadgar-dev-ca.key -sha256 -days 3650 \
  -out yadgar-dev-ca.crt \
  -subj "/CN=yadgar development root CA/O=yadgar" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "nameConstraints=critical,permitted;DNS:yadgar.internal,excluded;IP:0.0.0.0/0.0.0.0,excluded;IP:::/::"
```

**The `nameConstraints` line is the important one and is easy to leave out.**
This root goes into the system trust store, and its private key then lives in a
kind cluster. Without constraints, anything holding that key can mint a
trusted certificate for _any_ hostname — your bank, your registry, your identity
provider. Constrained, it can only sign names under `yadgar.internal`, so the
blast radius of a leaked development key is the development environment.

`yadgar.internal`, not `yadgar.localhost`, and that is a correction rather than a
preference — see "Move the development domain to `yadgar.internal`" below for the
measurement that forced it.

The IP exclusions are there because name constraints apply per name type:
constraining DNS alone leaves certificates with an IP SAN unconstrained.

`pathlen:0` stops it issuing intermediate CAs.

Verify before going further — if the extensions are missing, stop and redo:

```bash
openssl x509 -in yadgar-dev-ca.crt -noout -text | grep -A3 "X509v3 Name Constraints"
```

### 2. Store it in 1Password

Store it as a **Secure Note with named fields**, not as an SSH Key item. A
1Password SSH Key item hands back OpenSSH format
(a `BEGIN OPENSSH PRIVATE KEY` banner); `openssl` and `kubectl create secret tls`
both want PKCS#8 (a plain `PRIVATE KEY` banner), and the mismatch surfaces as an
unhelpful parse error. 1Password can generate a keypair but not a certificate, so
the material is minted by `openssl` above either way and 1Password only stores it.

```bash
op item create --category "Secure Note" --vault Private \
  --title yadgar-dev-ca \
  "private key[password]=$(cat yadgar-dev-ca.key)" \
  "certificate[text]=$(cat yadgar-dev-ca.crt)"
```

Field names matter — they are what the secret references resolve:

```bash
op read "op://Private/yadgar-dev-ca/private key" | head -1   # BEGIN PRIVATE KEY
op read "op://Private/yadgar-dev-ca/certificate" | head -1   # BEGIN CERTIFICATE
```

If the first prints an `OPENSSH PRIVATE KEY` banner instead, the item is the wrong
type; recreate it as a Secure Note.

The key is the secret. The certificate is public — it is committed to the nix repo
in step 4 so the trust statement is declarative, and that is fine.

### 3. Hand the key to cert-manager

**`make secrets` does this too, and it does not wait**: it creates the
`cert-manager` namespace itself, so the Secret is in place before Argo syncs
rather than after. Run this by hand only when loading the CA on its own, into a
cluster that is already up.

Straight from 1Password, so this works identically after a cluster rebuild with
no local files in play:

```bash
kubectl create secret tls yadgar-dev-ca \
  --namespace cert-manager \
  --cert <(op read "op://Private/yadgar-dev-ca/certificate") \
  --key  <(op read "op://Private/yadgar-dev-ca/private key")
```

Then destroy the local copies — 1Password is the durable one:

```bash
shred -u yadgar-dev-ca.key
rm -f yadgar-dev-ca.crt
cd - && rmdir "$OLDPWD" 2>/dev/null || true
```

This secret is deliberately **not** in git. Rebuilding the cluster means
recreating it from 1Password; the root itself survives, so nothing needs
re-trusting.

### 4. Trust the root, and name the host — nix repo

Both are host configuration, so they belong to the machine's own repo rather
than to `deploy`. Commit the **certificate only**.

```nix
# modules/nixos/yadgar-dev-tls.nix (or wherever host config lives)
{
  security.pki.certificateFiles = [ ./certs/yadgar-dev-ca.crt ];

  # .internal is reserved by ICANN (Board Resolution 2024.07.29.06) for
  # private use and no resolver treats it specially — so, unlike .localhost, the
  # same name can point at 127.0.0.1 here and at this host's bridge address from
  # a VM, with no client quietly deciding otherwise. Deliberately NOT .local,
  # which RFC 6762 reserves for mDNS — Avahi and systemd-resolved intercept it,
  # and the resulting resolution failures look like a cluster problem rather
  # than a naming one.
  networking.hosts."127.0.0.1" = [ "gateway.yadgar.internal" ];
}
```

Apply it yourself — `nixos-rebuild` is not run from here.

### 5. Check it end to end

```bash
curl -v https://gateway.yadgar.internal:18443/  # port 18443, per kind's mapping
```

A verified handshake with no `-k` is the pass condition. `-k` passing proves
nothing, since it is the check being skipped.

**Prove the check can fail**, per the invariant that a check which cannot fail
is worse than none:

```bash
curl -v https://127.0.0.1:18443/   # MUST fail: name constraint + SAN mismatch
```

If that succeeds, the certificate is not the one you think it is.

---

## Move the development domain to `yadgar.internal` (ledger 459)

Applies to an **existing** installation. A first-time setup follows the section
above, which already carries the new name and needs none of this.

### Why the name had to change, measured rather than assumed

**`gateway.yadgar.localhost` does not fail cleanly.** A name that failed cleanly
would have been easier to keep. RFC 6761 §6.3.3 says name resolution APIs "SHOULD
recognize localhost names as special and SHOULD always return the IP loopback
address". **SHOULD, not MUST** — and clients on one machine do not agree about
it, which is what the measurement below shows and what the text cannot.

Measured on a fresh Debian 13 guest (glibc 2.41, systemd-resolved) at
`192.168.122.101`, whose `/etc/hosts` points `gateway.yadgar.localhost` at this
host's bridge address `192.168.122.1`:

```bash
getent ahosts gateway.yadgar.localhost      # 192.168.122.1
python3 -c 'import socket; print(socket.getaddrinfo("gateway.yadgar.localhost", 18443))'
#   192.168.122.1

wget --ca-certificate=/root/yadgar-ca.crt https://gateway.yadgar.localhost:18443/
#   405 Method Not Allowed — it reached the gateway, verifying, with no -k

curl -v https://gateway.yadgar.localhost:18443/
#   resolved to ::1 and 127.0.0.1, refused by both
```

**The `/etc/hosts` entry is honoured, and `curl` is the outlier.** curl 8.14.1
implements §6.3.3's SHOULD internally and never consults NSS, so it answers
loopback for a `.localhost` name whatever the system is configured to say. `wget`
and Python ask NSS and get the bridge address.

**An earlier revision of this section claimed the opposite, and the claim was
false.** It said `getaddrinfo` returns loopback regardless and that no client on
another machine can therefore reach the gateway. Every piece of evidence for that
came from `curl`. Corrected here rather than deleted, because the wrong version
was carried into D71, `enrolment-token-design.md` and two pull requests.

**The surviving reason to move is narrower and still sufficient: `.localhost`
resolves for some clients and not for others on the same machine.** A name that
works under `wget` and Python but never under `curl` is harder to diagnose than
one that fails everywhere, and `curl` is the first tool anyone reaches for —
every off-host reachability check this file gates on goes through `curl`. The
`wget` line in the measurement above is diagnostic evidence, not a gate, so it
does not contradict that. A development domain whose behaviour
depends on which HTTP client the reader picked is not one to hand to a person
enrolling for the first time.

Connecting by address instead is not an option and cannot be made into one. Envoy
requires SNI matching the listener hostname, and the root CA excludes every IP
address by constraint, so a certificate for a bare address cannot be issued at
all. Both behaviours are correct on their own.

### Why `.internal`, and what it is and is not

`.internal` is reserved by **ICANN Board Resolution 2024.07.29.06** (29 July
2024): _"the Board reserves .INTERNAL from delegation in the DNS root zone
permanently to provide for its use in private-use applications."_ It implements
SSAC advisory SAC113.

**It is NOT an IETF special-use domain, and calling it one is wrong.**
`draft-davies-internal-tld` was never adopted — DNSOP's Call for Adoption closed
with no consensus — and `.internal` is absent from IANA's Special-Use Domain
Names registry. `.test` is in that registry, so `.test` carries the stronger
protocol backing of the two; it loses on being documented as a name for
throwaway testing, which this is not.

**That absence is the point.** No resolver gives `.internal` special treatment,
which is exactly the property `.localhost` lacked. There is no §6.3.3 for a
client to implement privately and no §6.2.4 negative-answer synthesis for a
caching resolver to apply, so the local network decides where the name goes and
nothing else quietly decides first.

**A private CA stays mandatory, and the `nameConstraints` plan is unaffected.**
The CA/Browser Forum Baseline Requirements define an Internal Name as one that
"cannot be verified as globally unique within the public DNS at the time of
certificate issuance because it does not end with a Top-Level Domain registered
in IANA's Root Zone Database", and prohibit publicly trusted CAs from issuing for
such names. No public CA can issue for `gateway.yadgar.internal` by structure,
not by policy preference, so the root minted below is not a shortcut around one.

**One operational wart, and it is not a resolution failure.** Chromium and Safari
have historically sent bare, schemeless `.internal` input in the address bar to
search rather than navigating to it (Chromium issue 375219954). An explicit
`https://` or a trailing `/` navigates. The current status in either browser is
unconfirmed here.

**Source caveat — confirm this one by hand.** `icann.org` returned HTTP 403 to
two direct fetch attempts, so the resolution wording above was corroborated
through search indexing and secondary sources rather than read off a rendered
ICANN page. Number, date and wording agree across those sources, and the IANA
registry, the CA/Browser Forum definition and the DNSOP non-adoption were each
read directly. The ICANN quote is the one claim here still owed a manual check.

**Still not `.local`**, which RFC 6762 reserves for mDNS — Avahi and
systemd-resolved intercept it, and the resulting failures read as a cluster
problem rather than a naming one.

On this host:

```bash
resolvectl query gateway.yadgar.localhost   # 127.0.0.1, "Data from: synthetic"
resolvectl query gateway.yadgar.internal    # Name not found — an ordinary name
```

The first is synthesised by the resolver, and no local configuration observed
here moves it. The second is a name the resolver is willing to look up, so
`/etc/hosts` or a DNS record decides where it goes. The `.localhost` line was
measured; the `.internal` line was not re-run after the name changed, and it is
the cheapest thing in this section to check.

### THE ORDER IS THE WHOLE PROCEDURE — read this before running anything

`infra/tls-app.yaml` syncs with `automated: { prune: true, selfHeal: true }`.
**Merging the manifest change IS the apply**, within a couple of minutes and with
no further command. So the CA has to be in the cluster and trusted by the host
BEFORE the pull requests merge, not after.

Two upstream behaviours set the sequence, and getting either backwards produces a
cluster that reports healthy while serving a certificate nothing trusts:

- **cert-manager's CA issuer does not enforce the issuing root's
  `nameConstraints`.** Its own documentation says so, under "Important
  Information" in <https://cert-manager.io/docs/configuration/ca/>: _"Other
  constraints - such as name constraints or the CA "max path length" - are not
  validated at the time of issuance"_. Asked for `gateway.yadgar.internal` while the
  old root is still installed, it SIGNS — successfully, with no error,
  Certificate `Ready=True`, Gateway `Programmed=True`. Every client then rejects
  the result for a permitted-subtree violation. The cluster looks correct and
  answers nothing that verifies.
- **Replacing the CA secret does not reissue the leaf.** Same page, same
  section: _"Updating the secret used for the CA certificate won't trigger
  re-issuance of leaf certificates"_. cert-manager reissues on a change to the
  Certificate's spec — `dnsNames`, `commonName`, `issuerRef` and the rest — not
  on the issuer's key material changing underneath it. So between
  loading the new root and merging the manifests, the gateway keeps serving the
  OLD certificate signed by the OLD root. That window is why the host trusts both
  roots across it, and it is also what makes the sequence safe to stop halfway.

**The window is safe on one precondition, and the precondition is not
automatic.** A spec change is not the only reissue trigger — **renewal** is one
too, and renewal does not care that a human is halfway through a rollover. The
Certificate carries `duration: 2160h` (90d) and `renewBefore: 720h` (30d), so
renewal fires 30 days before the leaf expires. If the step 4 to step 5 window
straddles that moment, cert-manager re-signs the OLD name with the NEW root, and
`gateway.yadgar.localhost` breaks before the merge, silently. Step 4 checks the
margin before opening the window.

### 1. Mint the new root

Same extensions as the original, one word different — `yadgar.internal` in the
constraint.

**`openssl` is not installed on this host**, and steps 1, 4 and 6 all need it. Get
a shell that has it first, and keep that shell for all three:

```bash
nix shell nixpkgs#openssl
```

```bash
cd "$(mktemp -d)"

openssl genrsa -out yadgar-dev-ca.key 4096

openssl req -x509 -new -nodes -key yadgar-dev-ca.key -sha256 -days 3650 \
  -out yadgar-dev-ca.crt \
  -subj "/CN=yadgar development root CA/O=yadgar" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "nameConstraints=critical,permitted;DNS:yadgar.internal,excluded;IP:0.0.0.0/0.0.0.0,excluded;IP:::/::"
```

**The constraint permits `yadgar.internal` and nothing else.** Not both names.
Widening it to permit the old subtree as well would make the rollover marginally
simpler and leave a permanently looser control behind, and the constraint is the
whole security argument for putting this root in a system trust store (D71). The
overlap is handled by trusting two roots for an afternoon instead.

Verify before going further — if the extensions are missing, stop and redo:

```bash
openssl x509 -in yadgar-dev-ca.crt -noout -text | grep -A3 "X509v3 Name Constraints"
```

### 2. Store it in 1Password beside the old one

A **new item**, not an edit of `yadgar-dev-ca`. The old item stays readable for
as long as rollback is possible, and `op read` addresses items by title, so two
items cannot share one.

```bash
op item create --category "Secure Note" --vault Private \
  --title yadgar-dev-ca-internal \
  "private key[password]=$(cat yadgar-dev-ca.key)" \
  "certificate[text]=$(cat yadgar-dev-ca.crt)"

op read "op://Private/yadgar-dev-ca-internal/private key" | head -1   # BEGIN PRIVATE KEY
op read "op://Private/yadgar-dev-ca-internal/certificate" | head -1   # BEGIN CERTIFICATE
```

Step 7 renames it to `yadgar-dev-ca` once the old root is retired.

### 3. Trust BOTH roots on the host, and resolve both names — nix repo

`security.pki.certificateFiles` is a list, and this is the only reason the
rollover has no outage. Commit the new certificate beside the old one:

```bash
cp yadgar-dev-ca.crt ~/git/nix/modules/nixos/certs/yadgar-dev-ca-internal.crt
```

Then, in `modules/nixos/yadgar-dev-tls.nix`, list both — temporarily:

```nix
security.pki.certificateFiles = [
  ./certs/yadgar-dev-ca.crt       # old root, DNS:yadgar.localhost — DELETE at step 7
  ./certs/yadgar-dev-ca-internal.crt  # new root, DNS:yadgar.internal
];
```

The `networking.hosts` entry in that file already names both hostnames — the nix
commit that accompanies this change adds `gateway.yadgar.internal` beside the old one
— so nothing there needs editing. If that commit is not on `master` yet, add the
name by hand rather than skipping the step.

```bash
sudo nixos-rebuild switch
```

Nothing is serving the new name yet. This step only makes the host willing to
believe it when it appears.

### 4. Replace the CA secret in the cluster

The secret's name does not change — `infra/tls/clusterissuer.yaml` references
`yadgar-dev-ca` in the `cert-manager` namespace, and that reference stays put.
Only the material inside it changes.

Every `kubectl` below names `--context kind-yadgar` on purpose. This host's
default context is a production cluster, and `delete secret` is not a command to
aim at it by accident.

**First, check the renewal margin**, per the precondition above. The window from
here to step 5 has to close before cert-manager reissues the leaf on its own, so
this has to be a real gate rather than a value for a human to eyeball — a check
that prints and exits 0 regardless of the date is worse than no check:

```bash
RENEWAL=$(kubectl --context kind-yadgar -n yadgar get certificate gateway-tls \
  -o jsonpath='{.status.renewalTime}')
[ -n "$RENEWAL" ] || { echo "no renewalTime — certificate not issued yet" >&2; false; }
MARGIN_DAYS=$(( ($(date -d "$RENEWAL" +%s) - $(date +%s)) / 86400 ))
echo "renewalTime: $RENEWAL — ${MARGIN_DAYS} days of margin"
[ "$MARGIN_DAYS" -ge 1 ] || {
  echo "STOP: renewal margin too small — reissue the leaf deliberately first, then retry" >&2
  false
}
# 2026-10-29T16:40:31Z when this was written — 58 days of margin, gate passes.
# Days, not hours, or the gate above stops you here. `false`, not `exit`, so a
# failure lands you back at the shell prompt rather than out of the `nix shell
# nixpkgs#openssl` from step 1 — this step still needs it.
```

> The `tls-preflight` Job in `infra/tls/ca-preflight.yaml` fails while this Secret
> is absent, and it sits one sync wave ahead of the rest of the Application. So
> the window between the delete and the create halts that wave. Run the two
> commands together, and expect `Application/tls` to report the preflight's
> failure until the new Secret is in place.
>
> Nothing has to be done to clear it afterwards. A failed Job is terminal and
> never re-runs itself, and Argo does not reattempt a sync that already failed
> against the same commit, so this used to need a `kubectl delete job` by hand.
> `infra/tls-app.yaml` now carries a `retry` block, and Argo re-runs the whole
> sync — recreating the Job — every few minutes until it passes.

```bash
kubectl --context kind-yadgar -n cert-manager delete secret yadgar-dev-ca

kubectl --context kind-yadgar create secret tls yadgar-dev-ca \
  --namespace cert-manager \
  --cert <(op read "op://Private/yadgar-dev-ca-internal/certificate") \
  --key  <(op read "op://Private/yadgar-dev-ca-internal/private key")
```

**Read the constraint back out of the cluster.** This is the check that fails
when the wrong material was loaded — a reloaded old root, or an `op read`
pointed at the wrong item. It greps the constraint's VALUE rather than the
`X509v3 Name Constraints` heading, which is present either way, so it exits
non-zero on the wrong root instead of printing something for a human to misread:

```bash
kubectl --context kind-yadgar -n cert-manager get secret yadgar-dev-ca \
  -o jsonpath='{.data.tls\.crt}' | base64 -d \
  | openssl x509 -noout -text \
  | grep "DNS:yadgar\.internal"
```

The certificate carries exactly one permitted DNS name, so one matching line is
the pass and nothing is the fail. Nothing printed, or a non-zero exit, means the
old root is still in there. Load the right material before going any further —
the whole sequence is built on this one object holding the new root.

Then destroy the local copies — 1Password is the durable one:

```bash
shred -u yadgar-dev-ca.key
rm -f yadgar-dev-ca.crt
cd - && rmdir "$OLDPWD" 2>/dev/null || true
```

**The gateway is still serving the old certificate at this point**, and the host
still trusts the old root, so `https://gateway.yadgar.localhost:18443/` still
works. Confirm it does before continuing — if it does not, something else is
wrong and merging on top of it will hide the cause:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://gateway.yadgar.localhost:18443/
```

That one is a liveness check and nothing more. It passes no matter which root
went into the secret, because the leaf being served is the old one either way —
so it is not, and cannot be, the gate on this step. The constraint read-back
above is the gate.

### 5. Merge the manifests

Two pull requests, and they are one change:

- `yadgarhq/deploy` — the Certificate's `dnsNames`/`commonName` and the Gateway
  listener's `hostname`
- `yadgarhq/gateway` — `gateway.hostname` in `chart/values.yaml`, which is the
  hostname on the HTTPRoute that attaches to that listener

Merging `deploy` changes the Certificate spec, which is exactly the trigger
cert-manager reissues on. Within a couple of minutes the gateway serves a
certificate for `gateway.yadgar.internal` signed by the new root.

**`gateway.yadgar.localhost` stops working at this instant, permanently.** The
name is no longer in the SAN and no longer matches the listener's SNI, so a
client asking for it gets a TLS failure rather than a redirect or a 404. Anything
still configured with the old address — a client `config.json`, a shell alias, a
saved `curl` — has to be repointed by hand. Nothing warns first.

If the `gateway` pull request lands and `deploy` does not, the HTTPRoute asks to
attach on a hostname the listener does not serve and the route simply does not
attach: no route, no crash. The reverse order is equally survivable. Neither is a
reason to merge them apart.

### 6. Check it end to end — from the host AND from a guest

**Do "Making the name resolve, on this host and on a VM" below before running
this step.** The guest half of this check needs the libvirt DNS record, and the
record is described in that section rather than as a numbered step because it is
a choice between three options. Step 7 is irreversible and gates on this step, so
the guest check has to happen here rather than after.

On the host:

```bash
kubectl --context kind-yadgar -n yadgar get certificate gateway-tls
kubectl --context kind-yadgar -n yadgar get secret gateway-tls \
  -o jsonpath='{.data.tls\.crt}' \
  | base64 -d | openssl x509 -noout -text | grep -A1 "Subject Alternative Name"

curl -v https://gateway.yadgar.internal:18443/
```

A verified handshake with no `-k` is the pass condition, and 405 rather than 404
means the route attached as well as the listener. `-k` passing proves nothing,
since it is the check being skipped.

**Then from a guest, and this one is the real gate.** The change exists so that
one name behaves the same way for every client on every machine, and the host
check does not exercise that — the host reached the old name too, and under
`curl`, which is the client the old name failed for off-host. Put the new root on
the guest first; it is public material, so copying it is fine:

```bash
op read "op://Private/yadgar-dev-ca-internal/certificate" \
  | ssh root@192.168.122.101 'cat > /root/yadgar-ca.crt'
```

Then, on the guest at `192.168.122.101`:

```bash
getent hosts gateway.yadgar.internal        # 192.168.122.1
curl -v --cacert /root/yadgar-ca.crt https://gateway.yadgar.internal:18443/   # 405
```

Both have to pass, and the `curl` is the one that matters: it is the client that
returned loopback for the old name no matter what the guest resolved. `getent`
returning nothing means the DNS record is missing or the guest's resolver is not
forwarding `.internal`; `curl` failing verification means the guest does not have
the new root. Neither is a reason to reach for `-k`, and
neither is a reason to run step 7.

**Prove the check can still fail**, per the invariant that a check which cannot
fail is worse than none:

```bash
curl -v https://127.0.0.1:18443/   # MUST fail: name constraint + SAN mismatch
```

### 7. Retire the old root — only after step 6 passes, host AND guest

This is the step that ends the rollback window, so do not run it on the same
afternoon as a change you are unsure of.

```bash
# nix repo
git rm modules/nixos/certs/yadgar-dev-ca.crt
git mv modules/nixos/certs/yadgar-dev-ca-internal.crt modules/nixos/certs/yadgar-dev-ca.crt
#   ... and point security.pki.certificateFiles at the renamed path alone,
#   ... and delete "gateway.yadgar.localhost" from networking.hosts
sudo nixos-rebuild switch

# 1Password
op item edit yadgar-dev-ca-internal --title yadgar-dev-ca   # after deleting the old item
```

The `git mv` matches the 1Password rename in the same block below — after step 7,
both the secret item and the committed certificate are named `yadgar-dev-ca`
again, with no `-internal` suffix surviving anywhere.

### Rolling back — put the old root back FIRST, then revert

**Reverting the two pull requests on their own does not roll anything back from
step 4 onward. It makes things worse.** Step 4 replaces `secret/yadgar-dev-ca`
in place, and `infra/tls/clusterissuer.yaml` still points at that one name, so
after step 4 the only signing root in the cluster is the `.internal`-constrained one.
The old root was overwritten, not kept beside it. Reverting puts `.localhost`
back in the Certificate's spec, which is exactly cert-manager's reissue trigger,
so it signs `gateway.yadgar.localhost` under a root permitted only under
`yadgar.internal` — a permitted-subtree violation. Trusting both roots does not help:
the certificate violates the constraint of the root that signed it. And the last
leaf signed by the old root is already gone, overwritten by step 5's reissue. The
result is a certificate that NEITHER name verifies against.

So a rollback restores the CA secret before it reverts anything. Until step 7
runs, the old item is still titled `yadgar-dev-ca` in 1Password, which is what
these references resolve — the same references step 3 of "The development TLS
edge" used to load it in the first place:

> The `tls-preflight` Job in `infra/tls/ca-preflight.yaml` fails while this Secret
> is absent, and it sits one sync wave ahead of the rest of the Application. So
> the window between the delete and the create halts that wave. Run the two
> commands together, and expect `Application/tls` to report the preflight's
> failure until the new Secret is in place.
>
> Nothing has to be done to clear it afterwards. A failed Job is terminal and
> never re-runs itself, and Argo does not reattempt a sync that already failed
> against the same commit, so this used to need a `kubectl delete job` by hand.
> `infra/tls-app.yaml` now carries a `retry` block, and Argo re-runs the whole
> sync — recreating the Job — every few minutes until it passes.

```bash
kubectl --context kind-yadgar -n cert-manager delete secret yadgar-dev-ca

kubectl --context kind-yadgar create secret tls yadgar-dev-ca \
  --namespace cert-manager \
  --cert <(op read "op://Private/yadgar-dev-ca/certificate") \
  --key  <(op read "op://Private/yadgar-dev-ca/private key")
```

Then revert the two pull requests. Argo reissues the leaf for
`gateway.yadgar.localhost` under the old root, which the host still trusts, and
the old name works again.

**This stops working the moment step 7 runs, for two separate reasons.** The
1Password item is renamed away, so `op://Private/yadgar-dev-ca` resolves to the
new root rather than the old one. And the nix commit deletes the old root from
`security.pki.certificateFiles` and `gateway.yadgar.localhost` from
`networking.hosts`, so the host neither trusts the old root nor resolves the old
name. After step 7 there is no rollback, only a roll forward.

### Making the name resolve, on this host and on a VM

`.internal` has no public DNS and never will — ICANN reserved it from the root
zone permanently. Something local has to answer, and this
is the part that is a choice rather than a consequence.

**On this host** it is settled: `networking.hosts."127.0.0.1"` in
`yadgar-dev-tls.nix`, which is what nsswitch reads before it reaches any
resolver. Already in the nix change that accompanies this.

**On a VM**, three options, in the order they were considered:

1. **`/etc/hosts` in each guest.** `192.168.122.1 gateway.yadgar.internal`. Works
   unconditionally — `files` is consulted by glibc and read by systemd-resolved
   too, so no resolver behaviour can veto it. It is also the only option for a
   machine that is not on `virbr0`. The cost is a manual step in every guest,
   which is precisely the step a person meeting the installer does not know to
   take.

2. **A DNS record on the libvirt network (RECOMMENDED).** libvirt runs dnsmasq on
   `virbr0` and hands it to every guest by DHCP, so one record serves every
   present and future VM with nothing to do inside the guest.

   The thing this option depends on is that the guest asks dnsmasq at all, and
   that is measurable without the record existing. On the Debian 13 guest at
   `192.168.122.101`, whose only DNS server is `192.168.122.1`:

   ```bash
   resolvectl statistics | grep 'Total Transactions'   # 50
   resolvectl query --cache=no probe-5030.yadgar.internal  # not found
   resolvectl statistics | grep 'Total Transactions'   # 52 — two queries went out

   resolvectl query --cache=no probe-5030.yadgar.localhost   # 127.0.0.1, ::1
   resolvectl statistics | grep 'Total Transactions'   # still 52 — nothing left
   ```

   **That counter is the whole point.** A `dig … @192.168.122.1` returning
   NXDOMAIN would prove nothing on its own — a caching resolver can synthesise a
   negative answer locally, and that also looks like NXDOMAIN. The transaction
   counter separates the two cases: the query left the guest and reached
   dnsmasq, while the `.localhost` query did not leave at all.

   **Read that measurement with one qualification.** It was taken with a `.test`
   probe name, which was the candidate at the time, and it has not been re-run
   under `.internal`. It transfers, and if anything it transfers upwards: RFC
   6761 §6.2.4 explicitly permits a caching resolver to answer `.test` names
   negatively by itself, so a different distribution could have made that
   counter lie. Nothing grants `.internal` even that — no RFC covers it and it
   is in no special-use registry — so there is no sanctioned local short-circuit
   for a resolver to take. Re-run the block above with the `.internal` name
   before relying on it. What is still unmeasured either way is whether dnsmasq
   answers the ADDED record positively, because the record does not exist yet.

   **This is a state-mutating change to shared infrastructure. Run it yourself.**
   Use `net-update`, not `net-edit` — it needs no restart of the network:

   ```bash
   sudo virsh net-update default add dns-host \
     "<host ip='192.168.122.1'><hostname>gateway.yadgar.internal</hostname></host>" \
     --live --config
   ```

   `--live` applies it to the running network, `--config` persists it to the
   definition. `dns-host` is a supported section of this host's `virsh`, and the
   `default` network currently has no `<dns>` element at all — libvirt adds one.

   The older path is `sudo virsh net-edit default`, adding the same `<host>`
   element inside a `<dns>` block after `<ip>`, then
   `sudo virsh net-destroy default && sudo virsh net-start default` to load it.
   **That `net-destroy` drops networking on every running guest on `virbr0`.**
   There is no reason to pay that with `net-update` available.

   **This record is undeclared host state, and nothing in git records that it
   exists.** It survives a reboot — the `default` network is `Persistent: yes`
   and `Autostart: yes` — but it does not survive rebuilding the machine from the
   nix repo, and a person rebuilding will find `.internal` resolving from the host
   and not from any guest, with nothing to grep for.

   Then verify from a guest, per step 6:

   ```bash
   getent hosts gateway.yadgar.internal        # 192.168.122.1
   curl --cacert /root/yadgar-ca.crt https://gateway.yadgar.internal:18443/   # 405
   ```

   If `getent` returns nothing, fall back to option 1 in that guest rather than
   debugging the resolver.

3. **Rejected: a resolver on the host serving a `yadgar.internal` zone.** A real
   dnsmasq or CoreDNS instance is a second name service on a machine that already
   has one, configured for a single A record. libvirt's dnsmasq is already
   running, already authoritative for this network, and already the guests'
   resolver.

The firewall rule in `kind.nix` that lets `192.168.122.0/24` reach `18443` is
unaffected — it matches on address and port, and nothing in it names a host.

---

## The `estate-front` runner (ledger 610, `yadgarhq/estate` stage 1)

`infra/arc.yaml`, `infra/estate-front-app.yaml` and `infra/estate-front-runner.yaml`
declare actions-runner-controller and the scale set `yadgarhq/estate`'s
`smoke.yaml` runs on. Argo applies all three. **Two things it cannot carry are
below. Neither is an operator step any longer — CI publishes the image and
`make secrets` loads the credential — but both still need a person when the
image digest moves.**

### What this is, in plain terms

Skip this if you already know ARC; it exists because the rest of this section
explains HOW without ever saying WHAT.

`yadgarhq/estate` runs a smoke suite after every deploy. That suite has to talk
to services **inside** the kind cluster, which a GitHub-hosted runner on the
public internet cannot reach. So the runner has to live in the cluster instead.

**actions-runner-controller (ARC)** is the GitHub-published Kubernetes operator
that does this. The shape is:

- A **controller** in `arc-systems` watches for scale-set definitions. Installed
  and Running.
- For each scale set it starts a **listener** pod, which holds the credential and
  long-polls GitHub asking "any jobs waiting for the label `estate-front`?" The
  listener runs no workflow code — that separation is why ADR-0563 tolerates the
  App's broad permissions.
- When a job appears, the listener creates a **runner pod** from a container
  image, which registers itself with GitHub, executes the job, and is destroyed.
  `minRunners: 0`, so nothing exists between jobs and an idle cluster is correct.

Hence two things Argo cannot carry, and each maps to one of those bullets. Both
were operator steps once. Neither is now:

1. **The runner image** is what the runner pod is made from. The stock
   `ghcr.io/actions/actions-runner` image has no Rust toolchain, and the smoke
   suite is a Rust test binary — so the estate builds its own with rustup baked
   in, rather than curl-piping a toolchain into a pod that holds the `estate`
   environment's secrets at job time. **CI builds and publishes it** (ADR-0579);
   what a person still does is move the digest pin, which is §1.
2. **The `estate-runner-github` Secret** is what the listener authenticates with.
   Without it the listener cannot start, so nothing ever asks GitHub for jobs,
   so every dispatched run queues until GitHub gives up on it. **`make secrets`
   creates it** (ADR-0580).

Neither can live in git: one is a container image, the other is a private key.
That is why this section exists at all — not because either is done by hand.

### Where this actually stands — measured 2026-09-06, read this first

Argo has applied its half, `make secrets` takes the other, and CI builds the
image. Nothing in this section is an operator step any more:

| thing                                       | state                                                  |
| ------------------------------------------- | ------------------------------------------------------ |
| `arc-systems` and `estate-front` namespaces | **exist**                                              |
| `deploy/arc-gha-rs-controller`              | **Running**, 1/1                                       |
| `AutoscalingListener` for `estate-front`    | **Running** in `arc-systems`                           |
| `AutoscalingRunnerSet/estate-front`         | **exists** — min 0, max 2                              |
| Secret `estate-front/estate-runner-github`  | **`make secrets` creates it**                          |
| `ghcr.io/yadgarhq/estate-runner`            | **published by CI** — `yadgarhq/actions`, see §1       |
| runner pods in `estate-front`               | **created per job**, destroyed after — `minRunners: 0` |

A cluster with no job in flight has no runner pod, and that is correct rather
than a fault. The listener is the row to check when nothing happens: it holds the
credential and does the asking, so without it every dispatched run queues in
silence.

**Step 2 lands before step 1 matters.** The Secret is what unblocks registration
and it is testable on its own: create it, and an `AutoscalingListener` pod
appears in `arc-systems`. The image is not pulled until a job is actually
dispatched to a runner, which cannot happen until the listener exists.

**The symptom on the GitHub side, so it is recognisable.** While the credential
was missing, every `smoke` run queued against `runs-on: estate-front` and was
eventually cancelled — measured 2026-09-05: **0 successful.** That is the
historical shape and it is kept because it is what the diagnosis below explains.
It no longer describes today: measured 2026-09-06, `smoke` runs reach the runner
and return verdicts, successes and failures both.

**WHY SO MANY ARE CANCELLED DESPITE `cancel-in-progress: false`.** The obvious
reading — that the setting is being ignored — is wrong, and so is the reading
this section carried first, that "no run ever starts, so there is nothing to
protect". Both are refuted by the run data.

A run that passes the concurrency gate OCCUPIES the group even while its jobs sit
`status: queued` waiting for a runner. It is the protected occupant, and
`cancel-in-progress: false` is what protects it — run 33971568146 (14:21:27Z) was
still alive four hours and fourteen dispatches later. GitHub's concurrency-blocked
status is a different one, `pending`, and that is the single waiting slot. Each
arriving run takes that slot and cancels whoever held it.

The timestamps prove it rather than suggest it: every cancellation's `updated_at`
is one second after the NEXT run's `created_at` (18:24:57/18:24:56,
18:25:04/18:25:03, 18:25:35/18:25:34). And run 33977748368, dispatched at
16:24:09Z, was not cancelled until 18:24:44Z — by the following batch, two hours
later. So the shape is global, not per batch: **one occupant, one pending slot,
everything else cancelled.** An earlier revision here claimed "four cancelled and
one stuck per batch"; the 16:23 batch actually left five cancelled and no
survivor.

**The concurrency block is correct and needs no change.** Fixing the runner fixes
the cancellations, because the occupant will then finish and free the group.

**The controller's own error, so you can confirm the diagnosis rather than trust
this table:**

```bash
kubectl -n arc-systems logs deploy/arc-gha-rs-controller --tail=300 | grep -i 'failed to resolve'
# Failed to initialize Actions service client for creating a new runner scale set
# failed to resolve app config: failed to get kubernetes secret
```

That message names the missing Secret and nothing else. When it stops appearing
and an `AutoscalingListener` pod is Running in `arc-systems`, step 2 worked.

`minRunners: 0`, so nothing is created until a job is queued. A cluster that has
synced this and stopped there is not broken.

**It does not LOOK broken either, and that is the part to know.** An earlier
revision of this section said `Application/estate-front-runner` goes Degraded
without the credential. It does not. Argo CD assesses a custom resource it has
no health check for as Healthy, and it has none for `actions.github.com` kinds:
v3.1.8 ships no `resource_customizations/actions.github.com/` directory, and
this cluster's `argocd-cm` declares no `resource.customizations.health.*` key at
all — both measured 2026-09-05. So a missing, misnamed or wrong-keyed Secret
leaves this Application reporting **Synced and Healthy**.

The evidence is in `arc-systems`, the controller's namespace, not in the runner's:

```bash
kubectl -n arc-systems logs deploy/arc-gha-rs-controller
kubectl -n arc-systems get pods   # an AutoscalingListener for estate-front, or none
```

What that failure looks like exactly is not written down here, because seeing it
now means BREAKING a working install: the listener is Running, measured
2026-09-06. Making the Degraded claim true would take
a `resource.customizations.health.actions.github.com_AutoscalingRunnerSet` entry
in `argocd-cm` — which is `yadgarhq/argocd`'s object
(`install/values.yaml`, `configs.cm`), not this repository's, so it is out of
scope here rather than declined.

### 1. Pin the digest CI published

**There is nothing to build by hand.** The Containerfile that used to sit in
this section as a fenced code block now lives at
`containers/estate-runner/Containerfile` in `yadgarhq/actions`, and
`.github/workflows/estate-runner-image.yaml` builds it. ADR-0579: an image the
estate depends on is built by CI, never by hand from a runbook. A Containerfile
that lives only in documentation is not a build — and a hand-pushed image is one
that passed no scan, carries no SBOM and holds no signature, which is the whole
apparatus bypassed at the one place it protects the build itself.

The push credential that used to be described here — a classic personal access
token with `write:packages`, used from a workstation — is no longer needed for
anything. The workflow authenticates with `GITHUB_TOKEN`. Do not mint one.

The visibility flip that used to be step three here is **done** (ledger 661), and
the workflow now re-verifies an anonymous pull on every run, so a package that
goes private reddens a build rather than surfacing as an ImagePullBackOff nobody
was told about.

**What a person still does, and it is one line.** The workflow's last step prints
the pin into the run's step summary:

```yaml
image: ghcr.io/yadgarhq/estate-runner@sha256:<digest>
```

Copy that line into `infra/estate-front-runner.yaml` and open a pull request.

**Read the digest off the run, never off a tag.** `:latest` and the dated tag
both move — the workflow promotes `:latest` onto each new build — so a tag names
whatever was published most recently rather than the thing anybody reviewed. The
digest is what cosign signed. Every pod in this estate reports `latest`
somewhere; comparing tags proves nothing.

**Bumping it is a deliberate edit, and that is the standing obligation.** The
workflow rebuilds weekly on cron (`17 4 * * 1`), so a new digest exists most
Mondays and none of them moves this repository. That is the same discipline as
`image.digest` in `yadgarhq/argocd`'s `versions/<module>.yaml`: the pin moves
when a person moves it. A cluster left unbumped keeps running the digest in git,
which is correct rather than broken — it simply ages. One difference worth
naming: those files carry a tag alongside the digest and this one does not. Here
the digest is the whole pin, and nothing should add a tag back beside it.

**How to check the roll landed, because Argo will not tell you.** Argo CD has no
health check for `actions.github.com` kinds, so `Application/estate-front-runner`
reports Synced and Healthy whether or not a runner ever comes up — a green status
is not evidence here. What is evidence is the digest a runner pod actually
resolved. Dispatch a smoke run, and while it holds a pod:

```bash
kubectl -n estate-front get pods \
  -o jsonpath='{range .items[*]}{.status.containerStatuses[*].imageID}{"\n"}{end}'
```

**Compare digests, never tags.** `.status.containerStatuses[].imageID` carries the
resolved `@sha256:` and must equal the pin in
`infra/estate-front-runner.yaml`. `.spec.containers[].image` only echoes what was
requested, and every pod in this estate says `latest` somewhere.

`minRunners: 0`, so there is no pod between jobs and nothing to check on an idle
cluster. That also means this change replaces no running pod when it syncs: the
next job builds its runner from the new pin.

The listener is a different object, and what happens to it is **expected rather
than observed** — said plainly so nobody takes it for a measurement. What IS
measured: the chart writes an `actions.github.com/values-hash` annotation over
the whole values block, and rendering the chart before and after shows that
changing the image line changes that hash. So the object Argo applies differs by
more than the image line. Whether the controller then recreates the
`AutoscalingListener` pod is an inference about how it reads that annotation,
and `helm template` cannot see it. Expect a new listener pod in `arc-systems`; a
job in flight during the sync is the case to avoid either way.

**The weekly rebuild does not bump the runner binary.** GitHub deprecates old
runner binaries and refuses registration for one far enough behind, so this
still has to be watched. The Containerfile's `FROM` is digest-pinned, which means
the cron refreshes the apt and rustup layers and leaves the runner version where
it is. Moving it is an edit to that `FROM` digest in `yadgarhq/actions`, then a
bump of the pin here. Two repositories, both reviewed — which is the point.

### 2. Create the `estate-runner-github` Secret — **`make secrets` does this now**

**There is nothing to run by hand any more.** The `secrets` target in the
Makefile creates this Secret alongside `yadgar-dev-ca` and `iam-keys`, reading
the key from the 1Password document `yadgar — yadgarhq-bot App private key`
(Private vault). `bootstrap` depends on `secrets`, so a recreated cluster gets
it without anyone remembering this section:

```bash
make secrets     # idempotent on its own
make bootstrap   # runs secrets first
```

It is idempotent the same way the other two are — `--dry-run=client -o yaml |
kubectl apply -f -` — so re-running it on a cluster that already has the Secret
is a no-op rather than an error.

**"No-op" holds only while the 1Password copy is unchanged.** If the document
holds a DIFFERENT key, this is a rotation rather than a no-op: the Secret carries
`kubectl.kubernetes.io/last-applied-configuration`, so the three-way merge does
update the value. That is the behaviour you want — but **the listener does not
re-read the Secret**. After any rotation, delete the listener pod in
`arc-systems` so it picks the new credential up; otherwise it keeps authenticating
with the old key and nothing says so. The private key goes through a `mktemp -d` file
created under `umask 077` and shredded by an `EXIT` trap; it never reaches argv
or shell history. `github_app_id` and `github_app_installation_id` are passed as
literals because neither is a secret.

**If `make secrets` fails on this step**, the 1Password document is missing or
renamed. Recreate it by the procedure below, keeping the title byte-identical —
the Makefile looks it up by title.

The rest of this section is the manual procedure, kept because it is what to do
when the 1Password document itself has to be recreated.

#### Recreating the 1Password document

The listener authenticates as the **`yadgarhq-bot`** App (app_id **4814165**,
installed organisation-wide). Not a PAT: a PAT carries a person's whole access
and outlives them.

**What the App holds**, measured 2026-09-05 —
`gh api /orgs/yadgarhq/installations` (installation **158692002**):
`actions: write`, `administration: write`, `contents: write`, `issues: write`,
`metadata: read`, `organization_self_hosted_runners: write`,
`pull_requests: write`, `workflows: write`.

**The permission this scale set needs is `administration: write`**, not
`organization_self_hosted_runners: write` as an earlier revision of this section
said. The latter registers a runner at the ORGANISATION.
`infra/estate-front-runner.yaml` sets
`githubConfigUrl: https://github.com/yadgarhq/estate`, so the registration is a
**repository** one, and GitHub asks for repository `administration: write` for
that. It is the reason that permission was granted.

**Say the cost out loud.** The same response reports
`repository_selection: "all"` — the installation is organisation-wide. So
`administration: write` reaches every repository in `yadgarhq`, not just
`estate`: settings, branch protection, collaborators, deletion. Registering one
repository's runner bought an organisation-wide administrative permission.
ADR-0563 is what makes that tolerable rather than fine — no job on this runner
ever receives the key, and the listener that does hold it runs in `arc-systems`
and executes no workflow code. That ADR's revisit trigger is the App being
SPLIT; it became broader instead, so the decision stands unchanged.

**The private key is never written to a file in this repository, and no manifest
references anything but the Secret's name.** It is the same key the release flow
uses (`RELEASE_APP_PRIVATE_KEY`), read out of 1Password.

The installation id is not a secret and can be re-derived:

```bash
# As an organisation owner. 158692002 on 2026-09-05.
gh api /orgs/yadgarhq/installations --jq '.installations[] | select(.app_id == 4814165) | .id'
```

**"WHAT KEY, AND WHY NOT JUST USE THE APP?" — it IS the App.** A GitHub App has
no password and no copyable token. It authenticates by signing a JWT with an RSA
**private key** and exchanging that JWT for a short-lived installation token, so
the private key IS the App credential; there is no App-without-a-key option. The
three fields below are all App identity — `github_app_id` (which App),
`github_app_installation_id` (which installation of it), `github_app_private_key`
(the proof).

ARC's `githubConfigSecret` accepts EITHER that App triple OR a single
`github_token` holding a PAT. This estate chose the App, for the reason in
`infra/estate-front-runner.yaml`: a PAT carries a person's whole access and
outlives them. A short-lived token minted per poll from a key held by a pod that
runs no workflow code is the narrower credential, which is also what makes
ADR-0563 tolerable.

**WHERE THE KEY IS: NOWHERE YOU CAN READ IT, so you will generate a new one.**
An earlier revision of this section said to `op read` it from 1Password and left
`<vault>/<item>` as a placeholder for the reader to fill in. There is nothing to
fill in. Searched 2026-09-05 across all 897 items in every vault: **no item holds
the `yadgarhq-bot` App private key.** The only copy is the organisation secret
`RELEASE_APP_PRIVATE_KEY`, and GitHub Actions secrets are write-only — neither
the API nor the web UI will show it back to you.

That is not a problem, because **a GitHub App may hold several private keys at
once**. Generating another does not invalidate the one the release flow uses.

1. Go to <https://github.com/organizations/yadgarhq/settings/apps/yadgarhq-bot>
   (App `yadgarhq-bot`, app_id `4814165`) — you must be an organisation owner.
2. Scroll to **Private keys** → **Generate a private key**. The browser downloads
   a `.pem` immediately; it is shown once and never again.
3. Move it to a path you control and keep the permissions tight.

**Then put it in 1Password, so the next person is not sent here again.** This is
the step whose absence made this section unrunnable:

```bash
op document create ./yadgarhq-bot.<date>.private-key.pem \
  --title 'yadgar — yadgarhq-bot App private key' --vault Private
```

The existing `yadgar iam — encryption key` and `yadgar iam — blind index key`
items are `DOCUMENT`-category entries in the `Private` vault; this follows them.
Once it is stored, the command below becomes
`op document get 'yadgar — yadgarhq-bot App private key' > ./yadgarhq-bot.pem`
and no download is needed again.

**If you would rather not add a key**, the alternative is to revoke and replace:
generate a new one, update the `RELEASE_APP_PRIVATE_KEY` organisation secret with
it, create the cluster Secret from it, and only then delete the old key from the
App. Do it in that order — deleting first breaks every release in the estate
until the secret is replaced.

```bash
# THE KEY GOES THROUGH A FILE, NOT argv AND NOT A PROCESS SUBSTITUTION. argv is
# visible in `ps` and lands in shell history, so `--from-literal` is out; a
# process substitution expands to `/dev/fd/63`, which `--from-file` handles
# inconsistently across kubectl versions. A real file with `umask 077`, deleted
# straight after, is the form that behaves the same everywhere.
umask 077
# Either the freshly downloaded file, or — once it is stored as above —
#   op document get 'yadgar — yadgarhq-bot App private key' > ./yadgarhq-bot.pem
cp ~/Downloads/yadgarhq-bot.*.private-key.pem ./yadgarhq-bot.pem

kubectl create namespace estate-front --dry-run=client -o yaml | kubectl apply -f -
kubectl -n estate-front create secret generic estate-runner-github \
  --from-literal=github_app_id=4814165 \
  --from-literal=github_app_installation_id=158692002 \
  --from-file=github_app_private_key=./yadgarhq-bot.pem \
  --dry-run=client -o yaml | kubectl apply -f -

shred -u ./yadgarhq-bot.pem
```

`--from-file=github_app_private_key=./yadgarhq-bot.pem` names the Secret key
explicitly. Dropping the `github_app_private_key=` prefix would name it after the
file, and the listener would report a missing field rather than a wrong one.

Argo does not manage this Secret and will not prune it. Rotating the key is the
same `create secret` command with `--dry-run=client -o yaml | kubectl apply -f -`
appended, then deleting the listener pod in `arc-systems` so it re-reads it.

### The fork pull-request approval policy — **NOTHING TO RUN**

Defence in depth behind the controls already enforced in git — see "How a fork
is kept off this runner" in `infra/estate-front-runner.yaml`. `yadgarhq/estate`
is a **public** repository, so this policy is the setting that matters, and it
is already at its strictest. Measured 2026-09-05, it reads
`all_external_contributors`, as it does on all fifteen public repositories in
the organisation:

```bash
gh api /repos/yadgarhq/estate/actions/permissions/fork-pr-contributor-approval
```

An earlier revision of this section asked an operator to set it. Do not; it is
set.

**It is a per-repository value, not an inherited one.** The ORGANISATION default
still reads `first_time_contributors`
(`gh api /orgs/yadgarhq/actions/permissions/fork-pr-contributor-approval`,
same date), so nothing about this is self-maintaining. What keeps a new
repository correct is `apply.sh` in `yadgarhq/docs`, which sets the repository
value at creation. A repository made by hand, outside that script, starts at the
organisation default.

**The organisation setting the design named — "fork pull-request workflows must
not run on self-hosted runners" — governs PRIVATE repositories only**, and the
runner group it also named cannot be created: `yadgarhq` is on the free plan and
`GET /orgs/yadgarhq/actions/runner-groups` returns only `Default`. Custom runner
groups are a Team or Enterprise feature. What replaces both is a
repository-scoped registration, which GitHub enforces.

### Resolving `gateway.yadgar.internal` — decided, and NOT a CoreDNS change

**Nothing to run.** The suite dials the external name so that SNI, the leaf and
the name-constrained chain are the ones a real client validates (ADR-0562). No
CoreDNS rewrite exists — verified 2026-09-05, `kube-system/coredns`'s Corefile
has no `rewrite` line — and none is added.

What ships instead is two objects this repository already owns: a stable
`yadgar-edge` Service on a pinned ClusterIP
(`infra/estate-front/edge-service.yaml`), and a `hostAliases` entry on the
runner pod that maps the name to it. Only RESOLUTION is redirected. Trust is
not: the client still sends `gateway.yadgar.internal` as SNI and still validates
the same certificate.

**Why not the Corefile, under ADR-0480.** That ConfigMap is written by kubeadm
when kind creates the cluster, so an Argo-managed copy makes two writers of one
object — ADR-0480's stated failure mode, not an analogy to it. A `kind delete`
and recreate resets it, and the half-built states in between are exactly what
that ADR exists to prevent. A Service and a pod spec are, by the same ADR,
unambiguously "what runs inside the cluster".

**What this does not cover, said plainly:** the name resolves in the runner pod
and nowhere else in the cluster. Stage 3's `estate-annex` scale set gets the
same `hostAliases` entry. If a THIRD consumer ever needs it, the general answer
is the Corefile, and it is the nix repo's to own:

```
rewrite name gateway.yadgar.internal yadgar-edge.envoy-gateway-system.svc.cluster.local
```

### Removing it again — the order matters, and one commit is the wrong shape

**Do not delete `infra/arc.yaml` and `infra/estate-front-runner.yaml` in the
same commit.** The scale-set chart puts an ARC finalizer,
`actions.github.com/cleanup-protection`, on three of the four objects it
renders — read out of `helm template` of `gha-runner-scale-set` 0.14.2 with this
repository's values block, 2026-09-05:

| object         | name                                |
| -------------- | ----------------------------------- |
| ServiceAccount | `estate-front-gha-rs-no-permission` |
| Role           | `estate-front-gha-rs-manager`       |
| RoleBinding    | `estate-front-gha-rs-manager`       |

Only the controller clears those finalizers. Prune the controller and the
finalizers stay, and each object sits `Terminating` until somebody patches the
finalizer off by hand. The two live in **separate Applications**, so nothing
about sync waves orders their PRUNES — waves order a sync, and these are two
deletions in two Applications.

The order for a person, one step at a time:

1. Delete `infra/estate-front-runner.yaml`, commit, and let Argo prune it.
2. Confirm the namespace is actually empty before going on:

   ```bash
   kubectl -n estate-front get autoscalingrunnersets,serviceaccounts,roles,rolebindings
   ```

   Anything still `Terminating` means the controller has not finished. Wait for
   it. Do not proceed while it is running, because it is the thing that will
   clear those finalizers.

3. Only then delete `infra/arc.yaml`, in a second commit.

If step 3 already happened by mistake, the recovery is to patch each stuck
object's finalizers to `[]` — which is a hand edit of cluster state, and the
reason this order is written down rather than discovered.

### What none of this proves

The runner registers and jobs land on it — both observed 2026-09-06, and an
earlier revision of this paragraph denied both. The NetworkPolicy in
`infra/estate-front/` is accepted by the API server and evaluated by nothing —
kindnet implements no NetworkPolicy — so the confinement is a specification, not
a control. That gap is `yadgarhq/docs` ledger 614, due 2026-10-03, and it belongs
to the nix repo.

Two things here are reasoned rather than observed, and are named so nobody takes
them for measurements. The teardown order is read off the rendered finalizers;
watching it go wrong means deleting a controller. And **nothing here has
observed a runner pod come up on the digest this repository now pins.** The pod
measured on 2026-09-06 resolved
`sha256:0d20f8ff5940906a99511934e572b0c64fea8cf6eec14809164ea59f7e12de07` — the
hand-built image the pin replaces — because the pin had not been applied yet.
What is verified about the new digest is that it is what CI published, scanned,
asserted and signed, and that it survives templating into the
`AutoscalingRunnerSet`. That it runs a smoke suite successfully is the next
dispatched run's job to show, by the `imageID` check in §1.

---

## The configuration repository, and the two-step cut-over that follows (ADR-0569, ADR-0570)

**NOTHING TO RUN FOR THE FIRST STEP.** `infra/config-app.yaml` points Argo at
`yadgarhq/config`, which renders seven ConfigMaps into the `yadgar` namespace at
sync wave -12. No pod mounts them yet, so a sync of this change adds objects and
rolls nothing.

**Why they land before anything reads them.** ADR-0569 gives a configuration knob
one source, no compiled-in default, and a refusal to boot when it is absent. A
service that reads a knob therefore cannot start until its ConfigMap exists, so
the ConfigMaps have to be in the cluster first. Wave -12 is the same wave the
credential bootstrap uses and for the same reason.

### What still has to happen, per service

The rotation schedule — `TLS_ROTATION_POLL_SECS` and `TLS_ROTATION_SPLAY_MAX_SECS`
— is the first knob moving. `yadgar-lifecycle` now reads it from
`/etc/yadgar/config/shared/shared.yaml` and the compiled-in `DEFAULT_POLL = 60s`
and `DEFAULT_SPLAY_MAX = 300s` are deleted. **The five services do not read it
yet**, because each pins that crate by an immutable git tag and the tag carrying
the reader has not been cut. Merging `yadgarhq/lifecycle` cuts it.

**A STAGED CUT-OVER, DELIBERATELY.** The value has to come from one source or
the other at every moment. Doing it in one change would need the chart to stop
setting the environment variable and the binary to start reading the file in the
same rollout, and a pod that picks up half of that has no schedule at all. So:

1. **Now.** The ConfigMaps exist. The services keep reading the environment
   variables their own charts set. Nothing changes at runtime.
2. **Per service, TWO pull requests, IN THIS ORDER** — in `gateway`, `iam`,
   `task`, `iam-db` and `task-db`. Two, not one, and the reason is mechanical.

   **THE CHART AND THE BINARY ARRIVE BY DIFFERENT PATHS AND DO NOT LAND
   TOGETHER.** Argo takes the module chart from the module repository at HEAD
   (`yadgarhq/argocd`, `applicationsets/modules.yaml`), so a chart change is live
   the moment the pull request merges. The image is not: it is pinned by digest
   in `yadgarhq/argocd`'s `versions/<repo>.yaml`, which `ci-release` writes
   minutes later from a separate pipeline in a sixth repository. A single pull
   request doing both halves therefore rolls the pod — the pod template changed —
   onto the OLD binary with the environment variables already deleted.
   `Schedule::from_env` takes its `None => Ok(default)` arm, so that pod runs
   `DEFAULT_POLL = 60s` and `DEFAULT_SPLAY_MAX = 300s`, the two constants this
   whole change exists to delete, with `shared.yaml` mounted and unread. An
   operator who set `pollSeconds: 17` gets 60. Normally the digest lands minutes
   later and the window closes itself. **If `ci-release` fails** — the image
   build, Trivy, or a `deployment` job that finds no release App — **the chart
   change is already live and the window does not close.** Nothing alerts on it;
   the only signal is that the `watching` count in the boot line does not move,
   because the old binary contributes no `Configuration` material.

   - **2a — ADD the new source, KEEP the old one.** `Cargo.toml`: move the
     `yadgar-lifecycle` pin from `v0.1.3` to the tag the lifecycle merge cuts.
     `src/main.rs`: replace `rotate::Schedule::from_env()` with
     `rotate::Configuration::mounted()`, and pass that value to both
     `.schedule()` and the service's `watch_set` so the document joins the watch
     set. `chart/templates/deployment.yaml`: add the `shared` and `<service>`
     ConfigMap volumes and their mounts, as DIRECTORIES under
     `/etc/yadgar/config/<name>`, with no `subPath` and no `optional: true`.
     **Leave the two `TLS_ROTATION_*` environment variables and the `tlsRotation`
     values block exactly where they are.** Old binary plus environment resolves;
     new binary plus file resolves; there is no arrangement of the two that
     resolves to nothing.
   - **2b — DELETE the old source, and only after the digest lands.** Check
     `yadgarhq/argocd`'s `versions/<repo>.yaml` carries the release 2a cut, then:
     `chart/templates/deployment.yaml` — delete the two `TLS_ROTATION_*`
     environment variables. `chart/values.yaml` — delete the `tlsRotation` block;
     it is the second source ADR-0569 forbids, and leaving it would be a value
     that looks live and is not. `README.md` — the two rows in the environment
     table become a pointer to `yadgarhq/config`. Chart only, no Rust, so this
     one lands whole on merge and there is nothing to wait for.

**The expected `watching` count in each service's boot line goes up by one** when
its 2a lands — one file, `shared/shared.yaml`, because no service has a knob of
its own yet. **That count is also the check that 2a completed.** A pod that came
back with the count unmoved is running the old binary, so its release did not
reach `yadgarhq/argocd`; do not open 2b until the count has moved. It goes up by two once one does. The base number is whatever
`<service>/src/rotate.rs`'s `watch_set` produces under this deployment's values;
it was not verified against a running estate, because the cluster was bare when
this was written.

### What a mistake looks like, at each level

| what is wrong                         | what you see                                                      |
| ------------------------------------- | ----------------------------------------------------------------- |
| the ConfigMap is absent               | the pod stays in `ContainerCreating`, with an event naming it     |
| it is mounted somewhere else          | the process exits, naming `/etc/yadgar/config/shared/shared.yaml` |
| the knob is deleted from the document | the process exits, naming `tlsRotation.pollSeconds` and the file  |
| the knob is left empty                | the same, reported as empty rather than as missing                |

None of these is a panic and none needs a backtrace to read: the message comes
back through `main`'s `Result` and is printed as `Error: <the sentence above>`.

**Editing `shared.yaml` restarts every service that mounts it.** That is ADR-0523
working as designed — the mounted file is in the watch set, a changed digest ends
the serve, and the pod drains and comes back on the new value. Do not read that
roll as a fault.

## Make the runner signature check required (ledger 742)

`.github/workflows/ci.yaml` gained a `signature` job. It runs
`scripts/runner_image_pinned.py --verify-signature`, which asks cosign whether
the digest in `infra/estate-front-runner.yaml` carries a signature by the
workflow identity that publishes it. **That job blocks nothing until this step
is taken**, and saying so is the point of this section: the `main` ruleset
requires exactly one status check, `ci / passed`, and this job is not inside it.
A red cross beside a mergeable pull request is a check people learn to ignore.

The structural half — is the reference a digest at all — is already required: it
is the `runner-image-pinned` pre-commit hook, and `ci / passed` runs every hook
in `.pre-commit-config.yaml`. Nothing below is needed for that half.

```bash
# READ FIRST. The API replaces the whole `rules` array, so the payload below has
# to match what is there. This is what it was on 2026-09-06.
gh api repos/yadgarhq/deploy/rulesets/21845322 --jq '.rules[] | select(.type=="required_status_checks")'

# THEN, as a repository admin. Adding one context to the existing list.
gh api --method PUT repos/yadgarhq/deploy/rulesets/21845322 \
  --input - <<'JSON'
{
  "name": "main",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "required_linear_history" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": true,
        "required_reviewers": [],
        "require_code_owner_review": false,
        "dismissal_restriction": { "enabled": false, "allowed_actors": [] },
        "require_last_push_approval": false,
        "required_review_thread_resolution": true,
        "require_extra_approval_for_unattributed_changes": true,
        "allowed_merge_methods": ["squash"]
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "do_not_enforce_on_create": false,
        "required_status_checks": [
          { "context": "ci / passed" },
          { "context": "signature" }
        ]
      }
    }
  ]
}
JSON
```

`signature`, not `ci / signature`. The two-part form belongs to a job that calls
a reusable workflow — `ci / passed` is `<caller job id> / <called job id>` — and
this job runs its own steps, so its check context is the job id alone. A context
that names no real check is not an error: the ruleset simply waits for a check
that never arrives and every pull request stays blocked, which reads like an
outage rather than a typo.

### What the check refuses, and what it cannot

It refuses a digest with no cosign signature by
`https://github.com/yadgarhq/actions/.github/workflows/estate-runner-image.yaml@refs/heads/main`
at `https://token.actions.githubusercontent.com`. That identity was read off the
live signing certificate rather than inferred from the workflow file. **A
rename of that workflow file, or a move of the runner build to another
repository, changes the identity and reddens this job** — the fix is to edit
`COSIGN_IDENTITY` in the script, deliberately, which is the behaviour wanted
from an exact identity rather than a regexp.

It cannot tell you the pin is the NEWEST published digest, and that is on
purpose. `estate-runner` is rebuilt every Monday, so "newest" moves with no
commit here; a freshness rule would redden weekly naming nobody's change.
Bumping the pin stays the deliberate two-repository edit the ledger 610 section
above describes.

---

## The administrative bootstrap token (ledger 638, ADR-0492, ADR-0517)

`infra/bootstrap/admin-bootstrap-token.yaml` mints the credential that lets the
FIRST administrator exist before any administrator exists to create one
(ADR-0492). It is generated on first sync, the same only-if-absent way as
`valkey-password` and `nats-auth` — see that file's own comments for the
mechanism and for why it is not a `make secrets` block.

**Unlike those three, this one is human-facing, and ADR-0517 requires it be
retrievable at least once through a documented command. This is that command:**

```bash
kubectl -n yadgar get secret admin-bootstrap-token -o jsonpath='{.data.token}' | base64 -d; echo
```

(the trailing `echo` is only because the token itself carries no newline, by
the same design as `valkey-password` — without it the shell prompt lands on
the same line as the last character and can read as truncation)

Run it, hand the value to whoever is creating the first administrator, and
nothing else needs to touch this Secret. Per ADR-0596 no other reason to read
`.data` exists — the Job that creates it, this section and the key name
already say what it is for.

**A cluster recreate mints a NEW token, and the old value stops being useful
the moment the old cluster is gone.** ADR-0596 names this shape for
`github-scm`: "never ABSENT... present-and-wrong, which no existence check can
discriminate." This Secret reaches it by a different road — `github-scm` is
never absent because `make bootstrap` unconditionally re-applies it, this one
because the Job's RBAC grants `create` alone, so on a rebuild it cannot tell
"the Secret survived" from "the Secret is new", it only ever sees
absent-then-present — but the operator-facing failure is the same one: the
Secret comes back Ready either way, so nothing in the cluster distinguishes a
token an operator can still use from one that has already been replaced.
**Re-run the command above after every recreate. A value copied down before
the last `kind delete cluster` is not the current token.**

(`estate-runner-github`'s ADR-0596 failure mode is a different bug — a
rotation the ARC listener pod does not re-read — not this one; see "The
`estate-front` runner" above.)
