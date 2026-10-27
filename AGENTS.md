# Project rules

## Commits

- **Always sign commits.** Use `git commit -S` with the OpenPGP smart card key
  `65620B56B1D64115DD3C8A37454E06981A7292D9` (Michal Borkowski, `michal@borkowski.nl`):
  `git -c user.signingkey=65620B56B1D64115DD3C8A37454E06981A7292D9 commit -S ...`
- Never use `--no-gpg-sign` or `-c commit.gpgsign=false`, and never commit unsigned.
- If signing fails (card not inserted, PIN prompt, gpg error), stop and tell the user. Do not
  fall back to an unsigned commit.
- After committing, verify with `git log --show-signature -1` and report the result.
- Only commit or push when the user asks for it.

## Local only

- `private/` (personal Home Assistant automations) is excluded in `.git/info/exclude`.
  Never stage it or add it to the repository.
- Never put credentials, tracking codes, postal codes or addresses of the user in the
  repository, tests or documentation.

## Integration

- Domain `dhl_parcel_nl`; keep `manifest.json` keys sorted (hassfest) and the version in sync
  with `CHANGELOG.md` and the README update notes.
- Run `ruff check` / `ruff format` and the Home Assistant test harness before committing.
- `README.md` in the repository root is the master. `custom_components/dhl_parcel_nl/README.md`
  must be a byte-identical copy of it: after every README change run
  `Copy-Item README.md custom_components\dhl_parcel_nl\README.md -Force`.

<!-- entire-agent:begin -->
Read .entire/agent-guide.md for this repository's workflow, source inspection, and verification guidance.
@.entire/agent-guide.md
<!-- entire-agent:end -->
