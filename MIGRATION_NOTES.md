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
from `infra/bootstrap/`, and every internal certificate from
`infra/internal-tls/`. Without `iam-keys` a fresh cluster reaches every pod Ready
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
`smoke.yaml` runs on. Argo applies all three. **Two things it cannot do are
below, and until both are done a dispatched smoke run has no runner.**

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
means installing this and nobody has. Making the Degraded claim true would take
a `resource.customizations.health.actions.github.com_AutoscalingRunnerSet` entry
in `argocd-cm` — which is `yadgarhq/argocd`'s object
(`install/values.yaml`, `configs.cm`), not this repository's, so it is out of
scope here rather than declined.

### 1. Build and push the runner image

`infra/estate-front-runner.yaml` names `ghcr.io/yadgarhq/estate-runner:0.1.0`,
which **does not exist yet**. The reasoning for a purpose-built image rather
than a `rustup` step at job time is written on that file; the short version is
that a `curl | sh` inside the pod holding the `estate` environment's secrets is
the opposite of what D61 asks for everywhere else.

```Containerfile
# Pin both halves. v2.337.0 was the current runner release on 2026-09-05;
# 1.98.0 is `rust-toolchain.toml` in yadgarhq/estate.
FROM ghcr.io/actions/actions-runner:2.337.0

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential pkg-config libssl-dev ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
USER runner

ENV RUSTUP_HOME=/home/runner/.rustup CARGO_HOME=/home/runner/.cargo
ENV PATH=/home/runner/.cargo/bin:$PATH
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
      | sh -s -- -y --default-toolchain 1.98.0 --profile minimal --component rustfmt --component clippy
```

**A push needs a credential, and it is NOT the App.** GHCR authenticates writes
with `write:packages`, which the `yadgarhq-bot` App does not hold — there is no
`packages` permission in the set measured under step 2 below. So this one step
uses a classic personal access token with `write:packages`. That is a **push**
credential used once from a workstation; it is not what the runner
authenticates with, and nothing puts it in the cluster. Step 2's "not a PAT"
applies to the runner's credential and is unaffected.

```bash
echo "$GHCR_PAT" | podman login ghcr.io -u <your-github-username> --password-stdin

podman build -t ghcr.io/yadgarhq/estate-runner:0.1.0 -f Containerfile .
podman push ghcr.io/yadgarhq/estate-runner:0.1.0
```

**Then make the package public — the pushed image is NOT pullable until you
do.** A package created by a first push to GHCR is private, and no pull
credential exists anywhere in this estate: `infra/estate-front-runner.yaml`
declares no `imagePullSecrets`, on purpose. A pushed-but-private package is a
runner pod in `ImagePullBackOff` with a **401** from the registry — which reads
like a typo in the image reference rather than a visibility setting, and is not
the not-found that a missing image gives.

There is no REST endpoint for this. The packages API offers get, list, delete
and restore only, so it is a settings page:

    https://github.com/orgs/yadgarhq/packages/container/estate-runner/settings
    → Danger Zone → Change visibility → Public

Public is what the other twelve container packages in this organisation already
are (`gh api "/orgs/yadgarhq/packages?package_type=container"`, 2026-09-05: all
twelve `public`), and the image carries nothing private — the upstream runner
plus a rustup toolchain.

The alternative, `imagePullSecrets` on the runner pod, was rendered rather than
dismissed: the field IS in the `AutoscalingRunnerSet` CRD's pod-spec schema, so
it survives into the pod. It is rejected because the `docker-registry` Secret it
needs holds a classic PAT with `read:packages`, living in the cluster and
outliving whoever minted it — the credential shape step 2 refuses, taken on for
an image whose contents are public anyway.

Verify it is pullable **anonymously**, which is what the kubelet will do:

```bash
TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:yadgarhq/estate-runner:pull" \
          | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
curl -sS -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" \
  -H 'Accept: application/vnd.oci.image.index.v1+json' \
  https://ghcr.io/v2/yadgarhq/estate-runner/manifests/0.1.0
```

`200` is done. `403` is the package still private — that is exactly what this
command returns for `estate-runner` today, and `200` is what it returns for the
already-public `yadgarhq/rust-build` (both run 2026-09-05, which is how this
check is known to discriminate).

Then read the digest and **open a pull request pinning it** —
`image: ghcr.io/yadgarhq/estate-runner:0.1.0@sha256:...`. A tag is what this
repository ships today because the image does not exist to be pinned; it is not
what it should keep shipping.

**GitHub deprecates old runner binaries.** A runner far enough behind is refused
at registration, so this image is rebuilt when the upstream runner is bumped.
That is the standing cost of choosing an image over a job step, and it is
stated rather than discovered.

### 2. Create the `estate-runner-github` Secret

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

```bash
# `<item>` is a placeholder: fill in whichever 1Password item holds the key the
# release flow reads as RELEASE_APP_PRIVATE_KEY. Nothing here can know it.
#
# THE KEY GOES THROUGH A FILE, NOT argv AND NOT A PROCESS SUBSTITUTION. argv is
# visible in `ps` and lands in shell history, so `--from-literal` is out; a
# process substitution expands to `/dev/fd/63`, which `--from-file` handles
# inconsistently across kubectl versions. A real file with `umask 077`, deleted
# straight after, is the form that behaves the same everywhere.
umask 077
op read "op://<vault>/<item>/private-key" > ./yadgarhq-bot.pem

kubectl create namespace estate-front --dry-run=client -o yaml | kubectl apply -f -
kubectl -n estate-front create secret generic estate-runner-github \
  --from-literal=github_app_id=4814165 \
  --from-literal=github_app_installation_id=158692002 \
  --from-file=github_app_private_key=./yadgarhq-bot.pem

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

The runner has never registered and no job has ever landed on it, because
proving either means installing. The NetworkPolicy in `infra/estate-front/` is
accepted by the API server and evaluated by nothing — kindnet implements no
NetworkPolicy — so the confinement is a specification, not a control. That gap
is `yadgarhq/docs` ledger 614, due 2026-10-03, and it belongs to the nix repo.

Two more things here are reasoned rather than observed, and are named so nobody
takes them for measurements. The teardown order is read off the rendered
finalizers; watching it go wrong means deleting a controller. And the image
steps are unexercised, because no `ghcr.io/yadgarhq/estate-runner` package
exists — `gh api "/orgs/yadgarhq/packages?package_type=container"` lists twelve
on 2026-09-05 and not this one. The visibility page and the digest pin are
written from the packages API's documented surface, which carries no visibility
endpoint, and from how those twelve already behave.

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
