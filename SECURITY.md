# Security Policy

Please do not publish exploitable vulnerabilities, credentials, session data, or private target URLs in public issues. Use GitHub private vulnerability reporting for this repository if enabled, or contact the maintainer privately through the contact method listed on the GitHub profile.

## Security notes

- Never commit .env files, provider credentials, SQLite files, or customer data.
- The auditor accepts HTTP(S) only, resolves and rejects private/reserved IP addresses, revalidates redirects, and limits response size and redirect depth.
- Put an authentication layer and appropriate network controls in front of any public deployment. This initial release is a single-user development foundation and has no account authentication or tenant isolation.
- External provider terms, quotas, and billing apply to API-backed search.
