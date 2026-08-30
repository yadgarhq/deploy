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
- **the CA secret**: step 3 below, from 1Password

That second one is the external-CA decision paying for itself. A root minted
inside the cluster by a SelfSigned issuer would have been regenerated here, and
the certificate would have quietly stopped chaining to the root this host trusts.

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
  -addext "nameConstraints=critical,permitted;DNS:yadgar.localhost,excluded;IP:0.0.0.0/0.0.0.0,excluded;IP:::/::"
```

**The `nameConstraints` line is the important one and is easy to leave out.**
This root goes into the system trust store, and its private key then lives in a
kind cluster. Without constraints, anything holding that key can mint a
trusted certificate for _any_ hostname — your bank, your registry, your identity
provider. Constrained, it can only sign names under `yadgar.localhost`, so the
blast radius of a leaked development key is the development environment.

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

  # RFC 6761 reserves .localhost and guarantees it resolves to loopback.
  # Deliberately NOT .local, which RFC 6762 reserves for mDNS — Avahi and
  # systemd-resolved intercept it, and the resulting resolution failures look
  # like a cluster problem rather than a naming one.
  networking.hosts."127.0.0.1" = [ "gateway.yadgar.localhost" ];
}
```

Apply it yourself — `nixos-rebuild` is not run from here.

### 5. Check it end to end

```bash
curl -v https://gateway.yadgar.localhost:18443/  # port 18443, per kind's mapping
```

A verified handshake with no `-k` is the pass condition. `-k` passing proves
nothing, since it is the check being skipped.

**Prove the check can fail**, per the invariant that a check which cannot fail
is worse than none:

```bash
curl -v https://127.0.0.1:18443/   # MUST fail: name constraint + SAN mismatch
```

If that succeeds, the certificate is not the one you think it is.
