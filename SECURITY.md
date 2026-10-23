# Security Policy

Please do not publish exploitable vulnerabilities, credentials, session data, or private target URLs in public issues. Use GitHub private vulnerability reporting for this repository if enabled, or contact the maintainer privately through the contact method listed on the GitHub profile.

## Security notes

- Never commit .env files, provider credentials, SQLite files, or customer data.
- All routes except the minimal `/api/health` endpoint require HTTP Basic authentication using username `verityagent` and the operator-managed `RANKFORGE_API_KEY` (at least 32 characters). The secret is not shipped to frontend code.
- URL fetches accept HTTP(S) only on ports 80/443, reject the hostname if any DNS answer is non-public, pin each connection to a validated address while retaining hostname-based TLS verification, disable automatic redirects, and revalidate each redirect hop.
- Compose binds the dashboard/API to loopback by default. The in-process rate limiter is a single-instance backstop, not a gateway replacement. A public deployment still requires TLS, a reverse proxy/API gateway with distributed rate limiting, monitoring, and egress controls blocking internal/metadata destinations.
- This is shared-operator authentication only. There are no per-user identities, role permissions, or tenant isolation; do not expose it as an unrestricted multi-user service.
- External provider terms, quotas, and billing apply to API-backed search.
