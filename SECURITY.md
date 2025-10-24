# Security Policy

## Supported Versions

We actively support the following versions with security updates:

| Version | Supported |
| ------- | --------- |
| 0.4.1 | ✅ |
| 0.4.0-beta | ✅ |
| < 0.4.0 | ❌ |

**Note:** Beta versions receive security patches on a best-effort basis. A formal security support policy will be established with v1.0.0.

## Reporting a Vulnerability

**Do not report security vulnerabilities through public GitHub issues.**

### Preferred Method: GitHub Security Advisories
1. Navigate to the **Security** tab in this repository
2. Click **"Report a vulnerability"**
3. Complete the form with the information detailed below

### Alternative: Direct Contact
Email security reports to: `[Your Email or Security Contact]`

### What to Include

Provide as much detail as possible:

- **Type of vulnerability** (authentication bypass, API key exposure, injection, etc.)
- **Affected component** (Discord client, reactive engine, memory manager, etc.)
- **Affected versions** (if known)
- **Steps to reproduce** (detailed proof-of-concept preferred)
- **Potential impact** (what can an attacker accomplish?)
- **Suggested fix** (if you have one)

### What to Expect

- **Initial Response:** Acknowledgment within 48 hours
- **Status Updates:** Regular progress reports during investigation
- **Resolution Timeline:** Critical issues patched within 7-14 days
- **Credit:** Recognition in security advisory (unless you prefer anonymity)

### Disclosure Policy

- **Coordinated disclosure:** 90-day embargo requested before public disclosure
- **Security advisories:** Published for all confirmed vulnerabilities
- **CVEs:** Requested for significant vulnerabilities affecting users

## Security Best Practices

### API Key Management

**Essential practices:**
- Store API keys exclusively in `.env` files (already in `.gitignore`)
- Use separate keys for development and production environments
- Rotate keys immediately upon suspected compromise
- Never hardcode keys in source code or configuration files

**Common mistakes to avoid:**
- Committing `.env` files to version control
- Sharing keys via chat platforms (Discord, Slack, email)
- Reusing the same key across multiple projects or environments
- Storing keys in bot YAML configuration files

### Discord Bot Token Security

**Essential practices:**
- Regenerate tokens immediately if exposed
- Enable 2FA on your Discord account
- Apply minimum required permissions (principle of least privilege)
- Audit bot's server list regularly

**Common mistakes to avoid:**
- Sharing tokens publicly or with untrusted parties
- Granting excessive Discord permissions "just in case"
- Adding bots to unfamiliar or untrusted servers

### Rate Limiting & Quota Management

**Essential practices:**
- Monitor API usage patterns regularly
- Configure appropriate rate limits for your deployment
- Set web search quota limits to control costs
- Review logs for anomalous activity

**Common mistakes to avoid:**
- Disabling rate limiting in production
- Ignoring quota warnings or usage spikes
- Sharing bot instances with untrusted users

### Server & Memory Isolation

**Essential practices:**
- Understand that memories are server-specific, not private
- Review memory contents periodically for sensitive data
- Limit bot deployment to trusted servers
- Use separate bot instances for different communities

**Common mistakes to avoid:**
- Assuming memory files are private (server admins can access them)
- Storing sensitive personal information in bot memory
- Deploying bots without vetting the server community

### Dependency Security

**Essential practices:**
- Update dependencies regularly: `pip install --upgrade -r requirements.txt`
- Monitor security advisories for `discord.py` and `anthropic` SDK
- Pin versions in production: `pip freeze > requirements.txt`

**Common mistakes to avoid:**
- Running outdated dependencies with known CVEs
- Ignoring dependency security warnings from GitHub or pip

## Known Security Considerations

### Message History Access
The bot can search and access full message history in channels with appropriate permissions. Sensitive conversations should occur in channels the bot cannot access. Use Discord's permission system to restrict bot visibility.

### LLM Prompt Injection
Users may attempt prompt injection attacks via Discord messages. The bot's system prompt includes safeguards, but no defense is foolproof. Monitor bot behavior for unexpected responses and report concerning patterns via security advisory.

### Memory Persistence
Bot memories are stored as plaintext Markdown files in the `memories/` directory. Anyone with filesystem access can read these files. Consider full-disk encryption for sensitive deployments.

### Web Search & External Content
The bot can fetch and display external web content, which creates potential phishing vectors. The bot includes citation display to show content sources—review these before trusting search results.

## Security Hardening Recommendations

### Development Environments
- Use Python virtual environments: `python -m venv venv`
- Run bot with standard user privileges (never elevated)
- Enable debug logging only in development
- Use dedicated Discord servers for testing

### Production Deployments
- Run bot in isolated containers (Docker recommended)
- Use systemd service with restricted user permissions
- Implement firewall rules limiting outbound connections
- Configure monitoring and alerting for anomalies
- Maintain regular backups of memories and configuration

### Self-Hosted Instances
- Keep host OS updated with security patches
- Use SSH key authentication (disable password login)
- Implement fail2ban or similar intrusion prevention
- Enable automatic security updates where possible
- Monitor system logs for suspicious activity

## Acknowledgments

We appreciate security researchers who responsibly disclose vulnerabilities. Contributors will be recognized in security advisories, release notes, and CHANGELOG.md (with consent).

Thank you for helping keep this framework secure.
