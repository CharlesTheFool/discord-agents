#!/usr/bin/env python3
"""
Debug script for testing Skills API integration.

Tests:
1. List all skills on the Anthropic account
2. Verify skill upload/existence
3. Test skills with code_execution tool (Bug #14 fix verification)
4. Reconcile .skills_cache.json with server state

Usage:
    python scripts/debug_skills_api.py

Requires ANTHROPIC_API_KEY environment variable.
"""

import os
import sys
import json
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from anthropic import Anthropic
except ImportError:
    print("ERROR: anthropic package not installed. Run: pip install anthropic")
    sys.exit(1)


def main():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set")
        sys.exit(1)

    client = Anthropic(api_key=api_key)

    print("=" * 60)
    print("Skills API Debug Script")
    print("=" * 60)

    # 1. List all skills on account
    print("\n[1] Listing skills on Anthropic account...")
    try:
        skills = client.beta.skills.list(betas=["skills-2025-10-02"])
        if skills.data:
            print(f"    Found {len(skills.data)} skills:")
            for skill in skills.data:
                print(f"    - {skill.display_title}: {skill.id}")
        else:
            print("    No skills found on account")
    except Exception as e:
        print(f"    ERROR listing skills: {e}")
        skills = None

    # 2. Check local .skills_cache.json
    cache_file = Path(".skills_cache.json")
    print(f"\n[2] Checking local cache ({cache_file})...")
    if cache_file.exists():
        with open(cache_file) as f:
            cache = json.load(f)
        print(f"    Found {len(cache)} entries in cache:")
        for file_hash, info in cache.items():
            status = info.get('status', 'uploaded')
            skill_id = info.get('skill_id', 'unknown')
            filename = info.get('filename', 'unknown')
            print(f"    - {filename}: {skill_id} (status: {status})")
    else:
        print("    No cache file found")
        cache = {}

    # 3. Test API call with skills + code_execution (Bug #14 verification)
    print("\n[3] Testing Skills + Code Execution API call...")
    try:
        response = client.beta.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=256,
            betas=["code-execution-2025-08-25", "skills-2025-10-02"],
            container={
                "skills": [
                    {"type": "anthropic", "skill_id": "xlsx", "version": "latest"}
                ]
            },
            tools=[{
                "type": "code_execution_20250825",
                "name": "code_execution"
            }],
            messages=[{
                "role": "user",
                "content": "Say 'Skills API working' and nothing else."
            }]
        )

        # Check response
        text_content = ""
        for block in response.content:
            if hasattr(block, 'text'):
                text_content += block.text

        if "working" in text_content.lower():
            print("    SUCCESS: Skills + code_execution working correctly!")
        else:
            print(f"    Response: {text_content}")
        print(f"    Stop reason: {response.stop_reason}")

    except Exception as e:
        print(f"    ERROR: {e}")
        print("\n    This error likely means:")
        print("    - Missing code_execution tool (Bug #14)")
        print("    - Invalid skill configuration")
        print("    - API quota/rate limit")

    # 4. Reconciliation suggestions
    print("\n[4] Reconciliation analysis...")
    if cache and skills and skills.data:
        server_ids = {s.id for s in skills.data}
        cache_ids = {
            info.get('skill_id')
            for info in cache.values()
            if info.get('status') != 'already_exists'
        }

        orphaned_cache = cache_ids - server_ids
        orphaned_server = server_ids - cache_ids

        if orphaned_cache:
            print(f"    WARNING: Cache has IDs not on server: {orphaned_cache}")
        if orphaned_server:
            print(f"    INFO: Server has skills not in cache: {orphaned_server}")
        if not orphaned_cache and not orphaned_server:
            print("    Cache and server are in sync")
    else:
        print("    Cannot reconcile (missing data)")

    print("\n" + "=" * 60)
    print("Debug complete")
    print("=" * 60)


if __name__ == "__main__":
    main()
