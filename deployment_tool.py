#!/usr/bin/env python3
"""
Discord Agents - Deployment Export/Import Tool

Backup and restore bot configuration and data across machines.

Usage:
    python deployment_tool.py export [--output PATH] [--exclude ITEMS] [--dev]
    python deployment_tool.py import --input PATH [--dry-run]

Examples:
    # Export production files only (what's in public repo)
    python deployment_tool.py export

    # Export everything including dev files (tests, prototype, docs)
    python deployment_tool.py export --dev

    # Export without logs (smaller backup)
    python deployment_tool.py export --exclude logs

    # Export to specific location
    python deployment_tool.py export --output ~/backups/bot-backup.zip

    # Preview import without modifying files
    python deployment_tool.py import --input backup.zip --dry-run

    # Import backup (creates safety backup first)
    python deployment_tool.py import --input backup.zip
"""

import argparse
import json
import zipfile
import shutil
from pathlib import Path
from datetime import datetime
import sys

# Production files (in public repo)
EXPORTABLE_ITEMS = {
    'env': '.env',
    'bots': 'bots/',
    'logs': 'logs/',
    'memories': 'memories/',
    'persistence': 'persistence/'
}

# Development-only files (not in public repo)
DEV_ONLY_ITEMS = {
    'tests': 'tests/',
    'prototype': 'prototype/',
    'docs': 'docs/',
    'deployment': 'deployment/'
}


def export_deployment(output_path=None, exclude=None, dev_mode=False):
    """
    Export deployment data to timestamped zip file.

    Args:
        output_path: Path for zip file (default: timestamped)
        exclude: Items to exclude from export
        dev_mode: If True, include dev files (tests, prototype, docs, deployment)

    Returns path to created file.
    """
    exclude = set(exclude or [])

    if not output_path:
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        suffix = '-dev' if dev_mode else ''
        output_path = f"deployment-backup_{timestamp}{suffix}.zip"

    output_path = Path(output_path)

    # Merge production and dev items if dev_mode enabled
    all_items = EXPORTABLE_ITEMS.copy()
    if dev_mode:
        all_items.update(DEV_ONLY_ITEMS)

    included_items = {k: v for k, v in all_items.items() if k not in exclude}

    print("=" * 60)
    print("Discord Agents - Export Tool")
    print("=" * 60)
    print(f"\nExporting to: {output_path}")
    print(f"Including: {', '.join(included_items.keys())}")
    if exclude:
        print(f"Excluding: {', '.join(exclude)}")
    print()

    try:
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Write manifest for version tracking
            manifest = {
                'created': datetime.now().isoformat(),
                'items': list(included_items.keys()),
                'version': '0.4.1',
                'framework': 'discord-agents'
            }
            zipf.writestr('manifest.json', json.dumps(manifest, indent=2))
            print("✓ Created manifest")

            # Export each item
            for item_name, item_path in included_items.items():
                path = Path(item_path)

                if not path.exists():
                    print(f"  ⚠ Skipping {item_name} (not found)")
                    continue

                if path.is_file():
                    zipf.write(path, path.name)
                    print(f"  ✓ {item_name}: {path.name}")
                else:
                    # Recurse directory, skip hidden files
                    file_count = 0
                    for file in path.rglob('*'):
                        if file.is_file() and not file.name.startswith('.'):
                            arcname = file.relative_to('.')
                            zipf.write(file, arcname)
                            file_count += 1

                    if file_count > 0:
                        print(f"  ✓ {item_name}: {file_count} files")
                    else:
                        print(f"  ⚠ {item_name}: empty directory")

        size_mb = output_path.stat().st_size / (1024 * 1024)

        print()
        print("=" * 60)
        print(f"✅ Export complete!")
        print(f"📦 File: {output_path}")
        print(f"📊 Size: {size_mb:.2f} MB")
        print("=" * 60)

        return str(output_path)

    except Exception as e:
        print(f"\n❌ Export failed: {e}", file=sys.stderr)
        sys.exit(1)


def import_deployment(input_path, dry_run=False):
    """
    Import deployment data from zip file.
    Creates safety backup before overwriting.
    Returns True on success.
    """
    input_path = Path(input_path)

    if not input_path.exists():
        print(f"❌ Error: {input_path} not found", file=sys.stderr)
        return False

    print("=" * 60)
    print("Discord Agents - Import Tool")
    print("=" * 60)
    print(f"\nImporting from: {input_path}")

    if dry_run:
        print("🔍 DRY RUN - No files will be modified")

    print()

    try:
        with zipfile.ZipFile(input_path, 'r') as zipf:
            # Read and display manifest
            try:
                manifest_data = zipf.read('manifest.json')
                manifest = json.loads(manifest_data)

                print("📋 Backup Information:")
                print(f"   Created: {manifest.get('created', 'Unknown')}")
                print(f"   Version: {manifest.get('version', 'Unknown')}")
                print(f"   Contains: {', '.join(manifest.get('items', []))}")
                print()

            except KeyError:
                print("⚠️  No manifest found (old or invalid backup)")
                manifest = {'items': list(EXPORTABLE_ITEMS.keys())}

            files_to_extract = [m for m in zipf.namelist() if m != 'manifest.json']

            if dry_run:
                print("Files that would be imported:")
                for member in files_to_extract:
                    print(f"  → {member}")
                print()
                print("=" * 60)
                print("🔍 Dry run complete - no files modified")
                print("=" * 60)
                return True

            # Create safety backup before destructive import
            backup_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_dir = Path(f".backup_{backup_timestamp}")

            print("🛡️  Creating safety backup...")
            backup_dir.mkdir(exist_ok=True)

            backed_up_count = 0
            for item_name, item_path in EXPORTABLE_ITEMS.items():
                if item_name not in manifest.get('items', []):
                    continue

                path = Path(item_path)
                if path.exists():
                    try:
                        if path.is_file():
                            shutil.copy2(path, backup_dir / path.name)
                            backed_up_count += 1
                        else:
                            shutil.copytree(path, backup_dir / path.name, dirs_exist_ok=True)
                            backed_up_count += sum(1 for _ in (backup_dir / path.name).rglob('*') if _.is_file())
                    except Exception as e:
                        print(f"  ⚠ Warning: Could not backup {item_path}: {e}")

            if backed_up_count > 0:
                print(f"  ✓ Backed up {backed_up_count} existing files to: {backup_dir}/")
            else:
                print("  ℹ No existing files to backup")
            print()

            # Extract files (overwrites existing)
            print("📦 Extracting files...")
            extracted_count = 0
            for member in files_to_extract:
                try:
                    zipf.extract(member, '.')
                    print(f"  ✓ {member}")
                    extracted_count += 1
                except Exception as e:
                    print(f"  ❌ Failed to extract {member}: {e}")

            print()
            print("=" * 60)
            print(f"✅ Import complete!")
            print(f"📥 Imported {extracted_count} files")
            if backed_up_count > 0:
                print(f"🛡️  Safety backup: {backup_dir}/")
            print("=" * 60)

            return True

    except zipfile.BadZipFile:
        print(f"❌ Error: {input_path} is not a valid zip file", file=sys.stderr)
        return False
    except Exception as e:
        print(f"❌ Import failed: {e}", file=sys.stderr)
        return False


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Discord Agents - Deployment Export/Import Tool',
        epilog='For detailed usage, see: docs/README.md'
    )
    subparsers = parser.add_subparsers(dest='command', required=True, help='Command to execute')

    # Export command
    export_parser = subparsers.add_parser(
        'export',
        help='Export bot configuration and data to zip file'
    )
    export_parser.add_argument(
        '--output', '-o',
        help='Output zip file path (default: deployment-backup_TIMESTAMP.zip)'
    )
    export_parser.add_argument(
        '--exclude',
        help='Comma-separated items to exclude (e.g., logs,memories)'
    )
    export_parser.add_argument(
        '--dev',
        action='store_true',
        help='Include dev files (tests, prototype, docs, deployment) for full workspace backup'
    )

    # Import command
    import_parser = subparsers.add_parser(
        'import',
        help='Import bot configuration and data from zip file'
    )
    import_parser.add_argument(
        '--input', '-i',
        required=True,
        help='Input zip file path'
    )
    import_parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview import without modifying files'
    )

    args = parser.parse_args()

    if args.command == 'export':
        exclude = args.exclude.split(',') if args.exclude else []
        export_deployment(args.output, exclude, dev_mode=args.dev)

    elif args.command == 'import':
        success = import_deployment(args.input, args.dry_run)
        sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
