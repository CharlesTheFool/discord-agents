# Deployment Setup Guide

**For:** Multi-machine development with private configuration syncing

This guide explains how to set up a private deployment repository that syncs your bot configurations, logs, and memories across multiple development machines while keeping the framework code in a public repository.

---

## Overview

**Problem:** You want to:
- Commit framework improvements to a public repo
- Keep your personal configs/data private
- Sync everything across multiple machines

**Solution:** Git submodule approach
- Framework → Public repo (code only)
- Deployment → Private repo (your data)
- Both sync independently

---

## Setup Process

### Step 1: Create Private GitHub Repository

1. Go to GitHub and create a **new private repository**
2. Name it: `discord-claude-deployment`
3. **DO NOT** add README, .gitignore, or license
4. Copy the repository URL (e.g., `git@github.com:yourusername/discord-claude-deployment.git`)

---

### Step 2: Prepare Deployment Directory Structure

In your framework directory:

```bash
# Create deployment directory structure
mkdir -p deployment/bots
mkdir -p deployment/logs
mkdir -p deployment/memories
mkdir -p deployment/persistence

# Move your personal files to deployment/
cp .env deployment/
cp bots/*.yaml deployment/bots/

# Move existing data (if any)
mv logs/* deployment/logs/ 2>/dev/null || true
mv memories/* deployment/memories/ 2>/dev/null || true
mv persistence/* deployment/persistence/ 2>/dev/null || true
```

---

### Step 3: Initialize Deployment Repository

```bash
cd deployment

# Initialize git repo
git init

# Create .gitignore for deployment repo
cat > .gitignore << 'EOF'
# Python cache
__pycache__/
*.pyc

# OS files
.DS_Store

# Temporary files
*.tmp
EOF

# Add and commit all files
git add .
git commit -m "Initial deployment configuration"

# Link to your private GitHub repo
git remote add origin <your-private-repo-url>

# Push to GitHub
git branch -M main
git push -u origin main

cd ..
```

---

### Step 4: Add as Submodule to Framework

In your framework directory:

```bash
# Add deployment as submodule
git submodule add <your-private-repo-url> deployment

# Commit the submodule reference
git add .gitmodules deployment
git commit -m "Add private deployment submodule"
```

---

### Step 5: Update Framework Repository

```bash
# Push framework changes to public repo
git push origin main
```

---

## Using on Other Machines

### Initial Setup on New Machine

```bash
# 1. Clone framework (public repo)
git clone <framework-repo-url>
cd discord-claude-framework

# 2. Initialize and fetch submodule (private repo)
# You'll need GitHub access to the private repo
git submodule update --init

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run bot
python bot_manager.py spawn alpha
```

---

### Daily Workflow

**Making framework changes:**
```bash
# Edit framework code (core/, tools/, etc.)
git add <changed-files>
git commit -m "Add feature X"
git push origin main
```

**Updating bot configs:**
```bash
cd deployment
# Edit bots/*.yaml or .env
git add .
git commit -m "Update bot configuration"
git push origin main
cd ..
```

**Syncing on other machine:**
```bash
# Pull framework changes
git pull origin main

# Pull deployment changes
cd deployment
git pull origin main
cd ..
```

---

## Directory Structure After Setup

```
discord-claude-framework/         # Public repo
├── .gitignore                    # Ignores deployment data
├── .gitmodules                   # Submodule reference
├── README.md
├── ARCHITECTURE.md
├── CHANGELOG.md
├── bot_manager.py
├── requirements.txt
│
├── core/                         # Framework code (public)
├── tools/                        # Tool implementations (public)
├── tests/                        # Tests (public)
├── docs/                         # Documentation (public)
│
├── bots/
│   └── alpha.yaml.example        # Template only (public)
│
├── .env.example                  # Template only (public)
│
└── deployment/                   # Git submodule → Private repo
    ├── .env                      # Your API keys (private)
    ├── bots/                     # Your bot configs (private)
    │   ├── alpha.yaml
    │   ├── beta.yaml
    │   └── slh-01.yaml
    ├── logs/                     # Your logs (private)
    ├── memories/                 # Your bot memories (private)
    └── persistence/              # Your databases (private)
```

---

## Config Loading Priority

The framework checks for configs in this order:

1. **`deployment/bots/{bot_id}.yaml`** (private submodule) ← PREFERRED
2. **`bots/{bot_id}.yaml`** (root override)
3. **`bots/{bot_id}.yaml.example`** (template fallback)

Environment files (.env):

1. **`deployment/.env`** (private submodule) ← PREFERRED
2. **`.env`** (root)
3. **`.env.example`** (template)

---

## Troubleshooting

### Submodule Not Cloning

**Problem:** `git submodule update --init` fails

**Solution:** Ensure you have access to the private repository:
```bash
# Check SSH key access
ssh -T git@github.com

# Or use HTTPS with credentials
git submodule set-url deployment https://github.com/yourusername/discord-claude-deployment.git
git submodule update --init
```

### Submodule Out of Sync

**Problem:** deployment/ shows changes you didn't make

**Solution:**
```bash
cd deployment
git status  # Check what changed
git pull origin main  # Pull latest
cd ..
git add deployment
git commit -m "Update deployment submodule reference"
```

### Accidentally Committed Private Data

**Problem:** Pushed .env or personal configs to public repo

**Solution:**
```bash
# Remove from git history (DANGEROUS - rewrites history)
git filter-branch --force --index-filter \
  'git rm --cached --ignore-unmatch .env' \
  --prune-empty --tag-name-filter cat -- --all

# Force push (only if repo is not shared)
git push origin --force --all

# Better: Rotate all exposed API keys immediately!
```

---

## Benefits of This Approach

✅ **Clean Separation**
- Framework code in public repo
- Personal data in private repo
- No sensitive data leaks

✅ **Multi-Machine Sync**
- Same setup across all machines
- Single `git pull` to update everything
- No manual file copying

✅ **Easy Updates**
- Framework updates: `git pull` in root
- Config updates: `git pull` in deployment/
- Independent versioning

✅ **No Overhead**
- Normal git workflow
- No special tools required
- Works with any git hosting (GitHub, GitLab, Bitbucket)

---

## Alternative: Without Submodule

If you prefer not to use git submodules:

**Option 1: Two separate repos, manual copying**
- Clone framework repo
- Clone deployment repo separately
- Manually copy files between them

**Option 2: Single private repo with everything**
- Keep everything in one private repo
- No public sharing of framework

**Option 3: Ignore deployment data (simplest for single machine)**
- Keep .env and configs in root
- Add to .gitignore
- Don't sync across machines

---

## Summary

**For multi-machine development with private configs:**
1. Create private `discord-claude-deployment` repo
2. Move personal files to `deployment/`
3. Add as submodule to framework
4. Push both repos
5. On other machines: `git clone` + `git submodule update --init`

**Daily workflow:**
- Framework changes: Commit to public repo
- Config changes: Commit to private deployment repo
- Sync: `git pull` in both directories

---

## Need Help?

- Check git submodule docs: `git help submodule`
- GitHub submodule guide: https://git-scm.com/book/en/v2/Git-Tools-Submodules
- Framework docs: [ARCHITECTURE.md](ARCHITECTURE.md)
