# Migration notes

Commands for a human to run. Nothing here is applied automatically: each step
either handles a private key or mutates cluster state, and both are decisions
rather than side effects.

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

Everything in the cluster is GitOps, so Argo rebuilds it — but two things do not
come back on their own:

- **the Argo CD install itself**: `make bootstrap` (needs `GITHUB_TOKEN`)
- **the CA secret**: step 3 of "The development TLS edge" below, from 1Password

That second one is the external-CA decision paying for itself. A root minted
inside the cluster by a SelfSigned issuer would have been regenerated here, and
the certificate would have quietly stopped chaining to the root this host trusts.

---

## The identity encryption keys (ledger 452)

`iam` encrypts stored names with AES-256-GCM and looks usernames up by an
HMAC-SHA256 blind index. It refuses to boot without both keys.

**Losing the encryption key is unrecoverable.** Every stored name becomes
permanently unreadable — not degraded, gone. Losing the blind-index key is nearly
as bad: no login can find its user again, because the index it computes no longer
matches the ones in the table. So the keys live in 1Password first and in the
cluster second, exactly like the CA below, and for the same reason: a cluster
rebuild must not destroy them.

Run **once**.

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

### 3. Load them into the cluster

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

### 4. Check it took

```bash
kubectl -n yadgar get secret iam-keys -o jsonpath='{.data}' | grep -o 'encryption.key'
kubectl -n yadgar logs deploy/iam | grep 'crypto keys loaded'
```

A pod that cannot read them does not start and says why — that is D69's rule
applied to key material, and it is deliberate: a service that cannot decrypt what
it stored is broken rather than degraded.

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
  -addext "nameConstraints=critical,permitted;DNS:yadgar.test,excluded;IP:0.0.0.0/0.0.0.0,excluded;IP:::/::"
```

**The `nameConstraints` line is the important one and is easy to leave out.**
This root goes into the system trust store, and its private key then lives in a
kind cluster. Without constraints, anything holding that key can mint a
trusted certificate for _any_ hostname — your bank, your registry, your identity
provider. Constrained, it can only sign names under `yadgar.test`, so the
blast radius of a leaked development key is the development environment.

`yadgar.test`, not `yadgar.localhost`, and that is a correction rather than a
preference — see "Move the development domain to `yadgar.test`" below for the
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

Wait until Argo has synced `cert-manager` and the namespace exists.

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
  security.pki.certificateFiles = [ ./yadgar-dev-ca.crt ];

  # RFC 6761 reserves .test for testing and, unlike .localhost, does NOT force
  # it to loopback — so the same name can point at 127.0.0.1 here and at this
  # host's bridge address from a VM. Deliberately NOT .local, which RFC 6762
  # reserves for mDNS — Avahi and systemd-resolved intercept it, and the
  # resulting resolution failures look like a cluster problem rather than a
  # naming one.
  networking.hosts."127.0.0.1" = [ "gateway.yadgar.test" ];
}
```

Apply it yourself — `nixos-rebuild` is not run from here.

### 5. Check it end to end

```bash
curl -v https://gateway.yadgar.test:18443/  # port 18443, per kind's mapping
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

## Move the development domain to `yadgar.test` (ledger 459)

Applies to an **existing** installation. A first-time setup follows the section
above, which already carries the new name and needs none of this.

### Why the name had to change, measured rather than assumed

`gateway.yadgar.localhost` cannot be reached from any machine except this one,
and no firewall rule fixes it. RFC 6761 §6.3.3 tells name resolution APIs to
"always return the IP loopback address" for `.localhost` names, and they obey: an
`/etc/hosts` entry on a Debian VM pointing `gateway.yadgar.localhost` at this
host's bridge address `192.168.122.1` was ignored, and `curl` tried `[::1]:18443`
then `127.0.0.1:18443` and was refused by both.

Connecting by address instead is not an option and cannot be made into one. Envoy
requires SNI matching the listener hostname, and the root CA excludes every IP
address by constraint, so a certificate for a bare address cannot be issued at
all. Both behaviours are correct on their own. Together they leave an off-host
client no path in.

`.test` is reserved by the same RFC for the opposite behaviour. §6.2.3 says name
resolution APIs "SHOULD send queries for test names to their configured caching
DNS server(s)" — there is no loopback mandate anywhere in §6.2. Confirmed on this
host rather than taken from the text:

```bash
resolvectl query gateway.yadgar.localhost   # 127.0.0.1, "Data from: synthetic"
resolvectl query gateway.yadgar.test        # Name not found — an ordinary name
```

The first is synthesised by the resolver and can never point anywhere else. The
second is a name the resolver is willing to look up, so `/etc/hosts` or a DNS
record decides where it goes.

### THE ORDER IS THE WHOLE PROCEDURE — read this before running anything

`infra/tls-app.yaml` syncs with `automated: { prune: true, selfHeal: true }`.
**Merging the manifest change IS the apply**, within a couple of minutes and with
no further command. So the CA has to be in the cluster and trusted by the host
BEFORE the pull requests merge, not after.

Two upstream behaviours set the sequence, and getting either backwards produces a
cluster that reports healthy while serving a certificate nothing trusts:

- **cert-manager's CA issuer does not enforce the issuing root's
  `nameConstraints`** (cert-manager issue #2446). Asked for `gateway.yadgar.test`
  while the old root is still installed, it SIGNS — successfully, with no error,
  Certificate `Ready=True`, Gateway `Programmed=True`. Every client then rejects
  the result for a permitted-subtree violation. The cluster looks correct and
  answers nothing that verifies.
- **Replacing the CA secret does not reissue the leaf.** cert-manager reissues on
  a change to the Certificate's spec — `dnsNames`, `commonName`, `issuerRef` and
  the rest — not on the issuer's key material changing underneath it. So between
  loading the new root and merging the manifests, the gateway keeps serving the
  OLD certificate signed by the OLD root. That window is why the host trusts both
  roots across it, and it is also what makes the sequence safe to stop halfway.

### 1. Mint the new root

Same extensions as the original, one word different — `yadgar.test` in the
constraint:

```bash
cd "$(mktemp -d)"

openssl genrsa -out yadgar-dev-ca.key 4096

openssl req -x509 -new -nodes -key yadgar-dev-ca.key -sha256 -days 3650 \
  -out yadgar-dev-ca.crt \
  -subj "/CN=yadgar development root CA/O=yadgar" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "nameConstraints=critical,permitted;DNS:yadgar.test,excluded;IP:0.0.0.0/0.0.0.0,excluded;IP:::/::"
```

**The constraint permits `yadgar.test` and nothing else.** Not both names.
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
  --title yadgar-dev-ca-test \
  "private key[password]=$(cat yadgar-dev-ca.key)" \
  "certificate[text]=$(cat yadgar-dev-ca.crt)"

op read "op://Private/yadgar-dev-ca-test/private key" | head -1   # BEGIN PRIVATE KEY
op read "op://Private/yadgar-dev-ca-test/certificate" | head -1   # BEGIN CERTIFICATE
```

Step 7 renames it to `yadgar-dev-ca` once the old root is retired.

### 3. Trust BOTH roots on the host, and resolve both names — nix repo

`security.pki.certificateFiles` is a list, and this is the only reason the
rollover has no outage. Commit the new certificate beside the old one:

```bash
cp yadgar-dev-ca.crt ~/git/nix/modules/nixos/certs/yadgar-dev-ca-test.crt
```

Then, in `modules/nixos/yadgar-dev-tls.nix`, list both — temporarily:

```nix
security.pki.certificateFiles = [
  ./certs/yadgar-dev-ca.crt       # old root, DNS:yadgar.localhost — DELETE at step 7
  ./certs/yadgar-dev-ca-test.crt  # new root, DNS:yadgar.test
];
```

The `networking.hosts` entry in that file already names both hostnames — the nix
commit that accompanies this change adds `gateway.yadgar.test` beside the old one
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

```bash
kubectl -n cert-manager delete secret yadgar-dev-ca

kubectl create secret tls yadgar-dev-ca \
  --namespace cert-manager \
  --cert <(op read "op://Private/yadgar-dev-ca-test/certificate") \
  --key  <(op read "op://Private/yadgar-dev-ca-test/private key")
```

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

### 5. Merge the manifests

Two pull requests, and they are one change:

- `yadgarhq/deploy` — the Certificate's `dnsNames`/`commonName` and the Gateway
  listener's `hostname`
- `yadgarhq/gateway` — `gateway.hostname` in `chart/values.yaml`, which is the
  hostname on the HTTPRoute that attaches to that listener

Merging `deploy` changes the Certificate spec, which is exactly the trigger
cert-manager reissues on. Within a couple of minutes the gateway serves a
certificate for `gateway.yadgar.test` signed by the new root.

**`gateway.yadgar.localhost` stops working at this instant, permanently.** The
name is no longer in the SAN and no longer matches the listener's SNI, so a
client asking for it gets a TLS failure rather than a redirect or a 404. Anything
still configured with the old address — a client `config.json`, a shell alias, a
saved `curl` — has to be repointed by hand. Nothing warns first.

If the `gateway` pull request lands and `deploy` does not, the HTTPRoute asks to
attach on a hostname the listener does not serve and the route simply does not
attach: no route, no crash. The reverse order is equally survivable. Neither is a
reason to merge them apart.

### 6. Check it end to end

```bash
kubectl -n yadgar get certificate gateway-tls
kubectl -n yadgar get secret gateway-tls -o jsonpath='{.data.tls\.crt}' \
  | base64 -d | openssl x509 -noout -text | grep -A1 "Subject Alternative Name"

curl -v https://gateway.yadgar.test:18443/
```

A verified handshake with no `-k` is the pass condition, and 405 rather than 404
means the route attached as well as the listener. `-k` passing proves nothing,
since it is the check being skipped.

**Prove the check can still fail**, per the invariant that a check which cannot
fail is worse than none:

```bash
curl -v https://127.0.0.1:18443/   # MUST fail: name constraint + SAN mismatch
```

### 7. Retire the old root — only after step 6 passes

This is the step that ends the rollback window, so do not run it on the same
afternoon as a change you are unsure of.

```bash
# nix repo
git rm modules/nixos/certs/yadgar-dev-ca.crt
#   ... and delete its line from security.pki.certificateFiles,
#   ... and delete "gateway.yadgar.localhost" from networking.hosts
sudo nixos-rebuild switch

# 1Password
op item edit yadgar-dev-ca-test --title yadgar-dev-ca   # after deleting the old item
```

Until this runs, rolling back is reverting the two pull requests: Argo reissues
the leaf under the old root, which the host still trusts, and the old name works
again.

### Making the name resolve, on this host and on a VM

`.test` has no public DNS and never will. Something local has to answer, and this
is the part that is a choice rather than a consequence.

**On this host** it is settled: `networking.hosts."127.0.0.1"` in
`yadgar-dev-tls.nix`, which is what nsswitch reads before it reaches any
resolver. Already in the nix change that accompanies this.

**On a VM**, three options, in the order they were considered:

1. **`/etc/hosts` in each guest.** `192.168.122.1 gateway.yadgar.test`. Works
   unconditionally — `files` is consulted by glibc and read by systemd-resolved
   too, so no resolver behaviour can veto it. It is also the only option for a
   machine that is not on `virbr0`. The cost is a manual step in every guest,
   which is precisely the step a person meeting the installer does not know to
   take.

2. **A DNS record on the libvirt network (RECOMMENDED).** libvirt runs dnsmasq on
   `virbr0` and hands it to every guest by DHCP, so one record serves every
   present and future VM with nothing to do inside the guest. Verified that the
   resolver is reachable and answering for this namespace at all — a query for
   `gateway.yadgar.test` against `192.168.122.1` returns NXDOMAIN, which is an
   answer rather than a refusal, so a local record is what is missing:

   ```bash
   dig gateway.yadgar.test @192.168.122.1   # status: NXDOMAIN, today
   ```

   **This is a state-mutating change to shared infrastructure. Run it yourself.**

   ```bash
   sudo virsh net-edit default
   ```

   Add inside `<network>`, after the `<ip>` block:

   ```xml
   <dns>
     <host ip='192.168.122.1'>
       <hostname>gateway.yadgar.test</hostname>
     </host>
   </dns>
   ```

   `net-edit` writes the definition but does not reload it. Taking effect costs
   the network:

   ```bash
   sudo virsh net-destroy default && sudo virsh net-start default
   ```

   **`net-destroy` drops networking on every running guest on `virbr0`.** Shut
   them down first, or accept that they lose their leases.

   Then, from a guest:

   ```bash
   getent hosts gateway.yadgar.test        # 192.168.122.1
   curl --cacert yadgar-ca.crt https://gateway.yadgar.test:18443/   # 405
   ```

   **NOT YET VERIFIED, and this is the honest limit of the recommendation.** RFC
   6761 §6.2.4 permits a caching resolver to answer `.test` negatively by default
   without asking anyone, so a guest running its own stub resolver could refuse
   the name before the query ever reaches dnsmasq. Nothing in the guest has been
   tested for that behaviour. If `getent` above returns nothing, fall back to
   option 1 in that guest rather than debugging the resolver.

3. **Rejected: a resolver on the host serving a `yadgar.test` zone.** A real
   dnsmasq or CoreDNS instance is a second name service on a machine that already
   has one, configured for a single A record. libvirt's dnsmasq is already
   running, already authoritative for this network, and already the guests'
   resolver.

The firewall rule in `kind.nix` that lets `192.168.122.0/24` reach `18443` is
unaffected — it matches on address and port, and nothing in it names a host.
