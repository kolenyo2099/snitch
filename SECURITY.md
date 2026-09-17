# Security policy

## Reporting

Open a private security advisory via GitHub ("Security" → "Report a vulnerability")
rather than a public issue. Include what an attacker could reach and the smallest
reproduction you have.

## Threat model in one paragraph

Snitch is a single-tenant, self-hosted tool: whoever can reach the API can steer
runs, read every alert and geometry, and (within config-write validation limits)
choose the outbound URLs the server will call. There is no multi-user isolation,
by design. The controls that matter are network reachability, the shared
password, and TLS termination in front of it.

## What the app does by default

- With no `ui.password` set, the API answers **loopback callers only** — safe on a
  desk, and remote callers are refused.
- With a password set, callers must present it (header or session cookie). Set
  `ui.trust_local: false` on a server, where a reverse proxy also arrives from
  loopback; `docker-compose.yml` does this for you.
- The API binds `127.0.0.1` in compose; expose it only behind a TLS reverse proxy.
- Credentials live in environment variables by name (`CDSE_CLIENT_ID`,
  `CDSE_CLIENT_SECRET`, `SNITCH_VLM_API_KEY`, `SMTP_PASSWORD`, `GEE_SERVICE_ACCOUNT_JSON`,
  `MATRIX_TOKEN`) and are never stored in the database or returned by the API.

## Known limitations

- One shared password, no per-user accounts or audit trail.
- Requests are logged without request bodies; alert payloads forwarded to
  notification channels necessarily leave the machine — point those channels only
  at endpoints you trust (the config editor enforces https for them).
