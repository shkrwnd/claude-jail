# Contributing

Thanks for your interest! This project is deliberately small and
stdlib-only — please keep it that way.

## Running the tests

```bash
PYTHONPATH=. python3 -m unittest discover tests -v
```

No dependencies to install. CI runs the same command on every PR.

## Ground rules

- **Respect the trust boundary.** `wrappers/` (container side) must stay
  stdlib-only and must never import from `server/` (host side). The container
  knows one protocol — JSON over localhost TCP — and nothing else. See
  [docs/architecture.md](docs/architecture.md).
- **No new dependencies** without prior discussion in an issue. Both sides
  currently run on a bare Python 3 install; that's a feature.
- **Fail closed.** Anything auth-related should deny on error, not approve.
- Add or update tests for behavior changes.

## Intended extension points

You usually don't need to touch core code:

- **Custom auth backend** — one class, selected via a dotted path in
  `AUTH_BACKEND=mypkg.MyBackend`.
- **New CLI wrapper** — a three-line shim in `wrappers/`.

Both are walked through in [docs/extending.md](docs/extending.md).

PRs adding generally useful backends or wrappers are welcome.

## Security issues

Do not open public issues for vulnerabilities — see [SECURITY.md](SECURITY.md).
