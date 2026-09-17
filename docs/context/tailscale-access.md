# Private dashboard access through Tailscale

## Current state

The primary compute host's Vesta process listens only on `127.0.0.1:33263`.
As checked on 2026-09-16, it has no Serve or Funnel configuration, no listener
on ports 80 or 443, and no public endpoint. This document is an access design
and runbook; it does not announce an active dashboard URL.

The verified Tailscale identity allowed to use the dashboard is the exact login
`aarav`. It is a Tailscale user record, not a device name or a display name.
The application does not grant access merely because a device belongs to the
tailnet.

## Required security boundary

When enabled, the application checks every request before route handling,
including review HTML, API calls, and media:

1. Gunicorn remains bound to loopback only.
2. The direct peer must be a loopback address.
3. `Tailscale-User-Login` must exactly equal `aarav`.
4. `POST`, `PUT`, `PATCH`, and `DELETE` requests with an `Origin` header must
   exactly match the configured canonical HTTPS origin. Requests without an
   `Origin` header are permitted only after the preceding identity checks so
   that authenticated command-line use remains possible.

Tailscale Serve removes client-provided identity headers and inserts the
authenticated identity before it proxies to its loopback target. Loopback-only
binding is essential: a service exposed on a LAN or Tailscale address would
allow a direct caller to forge the header. There is no missing-header or local
browser bypass in enabled mode. The app returns the same generic 403 response
for missing, wrong, and untrusted identity values.

This application layer is a backstop. A tailnet policy should also allow only
the `aarav` user to reach the host's TCP port 443. This has **not** been applied
because the control-plane policy is not available from the compute host. Do not
claim the network port is user-restricted until a policy administrator has
verified the rule without changing unrelated SSH or service access.

## HTTPS prerequisite and present blocker

Camera capture requires a browser secure context. Plain `http://` Tailscale
Serve is not suitable, even though it can carry Tailscale identity headers.
Tailscale Serve HTTPS must provide the TLS certificate for the host's MagicDNS
name before the dashboard is exposed.

This tailnet uses a custom control URL and currently advertises `CertDomains:
null` to the host. That is the client-visible signal that HTTPS certificate
provisioning is unavailable. Therefore do not run `tailscale serve --https`,
do not use Funnel, and do not enable the host's production auth environment
until the following succeeds:

```sh
tailscale status --json | jq '.CertDomains'
# Must include software-legion-pro-7-16irx9h.mesh.chudmesh.duckdns.org
```

For a Headscale-style control plane, the operator must provide a production
HTTPS-Serve implementation with ACME DNS-01 support and a publicly delegated
base domain. That means enabling the service's per-node certificate support,
making its authoritative DNS server reachable on UDP and TCP 53, and delegating
the base domain's NS records. A DuckDNS subdomain might not support the needed
delegation; use a separate owned, delegable base domain if necessary.

The alternative is an explicitly approved separate TLS design: a private
Tailscale-address listener and a valid certificate issued through an owned
domain's DNS-01 process, with an exact-user tailnet policy and a different
trusted identity check. It is not a drop-in replacement for Serve identity
headers and is intentionally not configured here. Do not install a local root
CA or present a self-signed certificate as a secure-context solution.

## Enable only after the prerequisite

First add the three values from
[`deploy/vesta-tailnet.env.example`](../../deploy/vesta-tailnet.env.example)
to the host's existing mode-600 `~/.config/vesta/host.env`, then restart only
the Vesta web service. This activates the strict application guard before the
network endpoint exists.

After a policy administrator has restricted TCP 443 to the exact `aarav` user,
and `CertDomains` is populated, create the private HTTPS endpoint:

```sh
tailscale serve --https=443 --bg http://127.0.0.1:33263
tailscale serve status --json
tailscale funnel status --json
```

The expected browser origin is:

```text
https://software-legion-pro-7-16irx9h.mesh.chudmesh.duckdns.org/review
```

The Funnel status must remain `{}`. Do not run `tailscale funnel`, and do not
reset Serve because that could erase another intentionally configured service.

Verify from Aarav's Tailscale device that the certificate is trusted and that
`/review` works. Then verify an unapproved tailnet identity receives 403 from
the application (or is rejected earlier by the policy), direct `http://` has no
listener, and `tailscale funnel status --json` is still empty. The browser on
the viewing device obtains its own camera stream; Vesta receives only the
chosen video chunks for compute.

## Disable and rollback

To stop only this root-path Serve endpoint, first inspect `tailscale serve
status --json` and then use the exact inverse command:

```sh
tailscale serve --https=443 off
```

Keep Gunicorn loopback-bound. Remove the three environment settings only after
the endpoint is disabled and the web service is restarted. Preserve all other
Serve, Funnel, tailnet policy, SSH, and runtime settings.
