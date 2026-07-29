# Security Policy

## Supported version

Security fixes are applied to the latest release on `main`. Version 0.3.x is the first deployment-ready line.

## Reporting a vulnerability

Use **Security -> Report a vulnerability** in the GitHub repository when private vulnerability reporting is available. If that control is unavailable, open a minimal issue asking the maintainer to establish a private contact channel.

Do not put tokens, credentials, private market data, exploit payloads, or personally identifiable information in a public issue.

Include:

- affected commit or image tag;
- deployment mode and configuration, with secrets removed;
- reproducible steps;
- impact;
- suggested mitigation, when known.

## Deployment boundary

This project is paper-only. It must not be extended with live order, withdrawal, wallet, private-key, leverage, futures, or broker-trading functionality without a separate threat model and security review.

For public deployment:

- leave mutation endpoints disabled, or require a strong admin bearer token;
- terminate TLS at a trusted ingress;
- keep the application port private;
- mount market data read-only;
- persist and back up `/var/lib/tradebot`;
- deploy immutable image tags;
- rotate an admin token immediately if it may have leaked.

Never submit real exchange, broker, wallet, or private-key credentials to this application. It neither needs nor supports them.
