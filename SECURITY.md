# Security policy

WaterfallHunter processes market data and may use optional third-party API credentials. Never commit secrets, runtime databases, production evidence, account identifiers, backups, private keys, or environment files.

## Reporting a vulnerability

Use GitHub **Private Vulnerability Reporting** for this repository. Do not open a public issue for an unpatched vulnerability and do not include live credentials or private production data in a report.

A useful report includes:

- affected component and exact revision when known;
- reproduction steps or a minimal proof of concept;
- expected security impact and prerequisites;
- whether Production, CI/CD, persistence, or credentials are affected;
- any safe mitigation already identified.

Do not exploit public infrastructure, access data that is not yours, persist access, or broaden a proof of concept beyond what is necessary to demonstrate the issue.

## Supported security boundary

The repository is `SIGNAL_ONLY`. `LIVE_TRADING_ENABLED=false` is mandatory and the supported runtime does not place or cancel exchange orders. Any future order-execution capability requires a separately reviewed safety design and implementation outside the current runtime boundary.

GitHub secret scanning with push protection and Dependabot security updates are enabled. CI also rejects tracked runtime/secret-like files and scans tracked text for common credential patterns.
