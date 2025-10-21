# Security Policy

## Supported Versions

We actively support the following versions with security updates:

| Version | Supported          |
| ------- | ------------------ |
| 0.4.0-beta | :white_check_mark: |
| < 0.4.0 | :x:                |

**Note:** Beta versions receive security patches on a best-effort basis. Once v1.0.0 is released, we will maintain a formal security support policy.

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, report security issues privately:

### Preferred Method: GitHub Security Advisories
1. Go to the **Security** tab in this repository
2. Click **"Report a vulnerability"**
3. Fill out the form with details below

### Alternative: Direct Contact
If you prefer, you can email security reports to: [Your Email or Security Contact]

### What to Include

Please provide as much information as possible:

- **Type of vulnerability** (e.g., authentication bypass, API key exposure, SQL injection)
- **Affected component** (e.g., Discord client, reactive engine, memory manager)
- **Affected versions** (if known)
- **Steps to reproduce** (detailed PoC if possible)
- **Potential impact** (what could an attacker accomplish?)
- **Suggested fix** (if you have one)

### What to Expect

- **Initial Response:** Within 48 hours, we'll acknowledge receipt
- **Status Updates:** We'll keep you informed as we investigate
- **Resolution Timeline:** We aim to release fixes within 7-14 days for critical issues
- **Credit:** We'll credit you in the security advisory (unless you prefer to remain anonymous)

### Disclosure Policy

- **Coordinated disclosure:** We request 90 days before public disclosure
- **Security advisories:** We'll publish advisories for confirmed vulnerabilities
- **CVEs:** We'll request CVEs for significant vulnerabilities affecting users

## Security Best Practices for Users

### API Key Management

✅ **DO:**
- Store API keys in `.env` file only
- Add `.env` to `.gitignore` (already configured)
- Use separate API keys for development and production
- Rotate keys immediately if compromised
- Use environment variables, never hardcode keys

❌ **DON'T:**
- Commit `.env` files to version control
- Share API keys in Discord, Slack, or other chat platforms
- Use the same key across multiple projects
- Store keys in bot configuration YAML files

### Discord Bot Token Security

✅ **DO:**
- Regenerate token immediately if exposed
- Enable 2FA on your Discord account
- Restrict bot permissions to minimum required
- Review bot's server list regularly

❌ **DON'T:**
- Share bot tokens publicly
- Grant unnecessary Discord permissions
- Add your bot to untrusted servers

### Rate Limiting & Quota Management

✅ **DO:**
- Monitor your bot's API usage regularly
- Set appropriate rate limits in configuration
- Use web search quota limits to control costs
- Review logs for unusual activity patterns

❌ **DON'T:**
- Disable rate limiting in production
- Ignore quota warnings
- Share your bot instance with untrusted users

### Server & Memory Isolation

✅ **DO:**
- Understand that memories are server-specific
- Review memory contents periodically
- Limit bot to servers you trust
- Use separate bot instances for different communities

❌ **DON'T:**
- Assume memory is private (server admins can access it)
- Store sensitive personal information in bot memory
- Add bot to servers without vetting them first

### Dependency Security

✅ **DO:**
- Keep dependencies updated: `pip install --upgrade -r requirements.txt`
- Review security advisories for discord.py and anthropic SDK
- Pin versions in production: `pip freeze > requirements.txt`

❌ **DON'T:**
- Use outdated dependencies with known vulnerabilities
- Ignore dependency security warnings

## Known Security Considerations

### Message History Access
- Bot can search and access full message history in channels it has access to
- Sensitive conversations should occur in channels the bot cannot see
- Consider using Discord's permission system to restrict bot access

### LLM Prompt Injection
- Users may attempt prompt injection attacks via Discord messages
- Bot's system prompt includes safeguards but is not foolproof
- Monitor bot behavior for unexpected responses
- Report concerning patterns via security advisory

### Memory Persistence
- Bot memories are stored in plaintext Markdown files
- Files are stored locally in `memories/` directory
- Anyone with file system access can read memories
- Consider full-disk encryption for sensitive deployments

### Web Search & External Content
- Bot can fetch and display external web content
- Malicious users might attempt to phish via crafted search results
- Bot includes citation display to show content sources
- Review web search results before trusting them

## Security Hardening Recommendations

### For Development
- Use virtual environments: `python -m venv venv`
- Don't run bot with elevated privileges
- Enable debug logging only in development
- Use separate Discord servers for testing

### For Production
- Run bot in isolated container (Docker recommended)
- Use systemd service with restricted user permissions
- Enable firewall rules limiting outbound connections
- Implement monitoring and alerting for anomalies
- Regular backups of memories and configuration

### For Self-Hosting
- Keep host OS updated and patched
- Use SSH key authentication (disable password login)
- Implement fail2ban or similar intrusion prevention
- Enable automatic security updates
- Monitor system logs for suspicious activity

## Acknowledgments

We appreciate security researchers who responsibly disclose vulnerabilities. Contributors will be recognized in:
- Security advisories
- Release notes for patched versions
- CHANGELOG.md (if they consent)

Thank you for helping keep Discord-Claude Bot Framework secure!
