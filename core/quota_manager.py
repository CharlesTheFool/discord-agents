"""
Quota Manager - Files API usage tracking and warnings.

Monitors Files API quota consumption (100GB limit) and provides warnings
when approaching storage limits.
"""

import logging
from typing import Dict

logger = logging.getLogger(__name__)


class QuotaManager:
    """Tracks Files API usage and provides quota warnings."""

    QUOTA_LIMIT_BYTES = 100 * 1024 * 1024 * 1024  # 100GB

    def __init__(self, attachment_db: "AttachmentDatabase"):
        """
        Initialize quota manager.

        Args:
            attachment_db: Database instance for querying quota stats
        """
        self.attachment_db = attachment_db

    async def get_usage_stats(self) -> Dict:
        """
        Query current Files API usage statistics.

        Returns:
            Dict with keys:
                - used_gb: Gigabytes used
                - remaining_gb: Gigabytes remaining
                - percent_used: Percentage of quota consumed
                - total_files: Number of files uploaded
        """
        stats = await self.attachment_db.get_quota_stats()

        # Convert bytes to GB
        used_gb = stats["bytes_used"] / (1024 ** 3)
        remaining_gb = stats["bytes_remaining"] / (1024 ** 3)

        return {
            "used_gb": used_gb,
            "remaining_gb": remaining_gb,
            "percent_used": stats["percent_used"],
            "total_files": stats["total_files"]
        }

    async def can_upload(self, file_size_bytes: int) -> bool:
        """
        Check if adding this file would exceed 100GB quota.

        Args:
            file_size_bytes: Size of file to upload

        Returns:
            True if there's room for the file
        """
        stats = await self.attachment_db.get_quota_stats()
        new_total = stats["bytes_used"] + file_size_bytes

        return new_total <= self.QUOTA_LIMIT_BYTES

    async def warn_if_approaching_limit(self) -> None:
        """
        Log warnings when approaching quota limits.

        Thresholds:
        - 80%: Warning
        - 90%: High warning
        - 95%: Critical warning
        """
        stats = await self.get_usage_stats()
        percent = stats["percent_used"]

        if percent >= 95:
            logger.error(
                f"CRITICAL: Files API quota at {percent:.1f}% "
                f"({stats['used_gb']:.2f} GB / 100 GB). "
                f"Remaining: {stats['remaining_gb']:.2f} GB"
            )
        elif percent >= 90:
            logger.warning(
                f"HIGH WARNING: Files API quota at {percent:.1f}% "
                f"({stats['used_gb']:.2f} GB / 100 GB). "
                f"Remaining: {stats['remaining_gb']:.2f} GB"
            )
        elif percent >= 80:
            logger.warning(
                f"WARNING: Files API quota at {percent:.1f}% "
                f"({stats['used_gb']:.2f} GB / 100 GB). "
                f"Remaining: {stats['remaining_gb']:.2f} GB"
            )

    def format_size(self, bytes: int) -> str:
        """
        Convert bytes to human-readable size.

        Args:
            bytes: Size in bytes

        Returns:
            Human-readable string (e.g., "45.2 GB", "1.3 MB")
        """
        if bytes < 1024:
            return f"{bytes} B"
        elif bytes < 1024 ** 2:
            return f"{bytes / 1024:.1f} KB"
        elif bytes < 1024 ** 3:
            return f"{bytes / (1024 ** 2):.1f} MB"
        else:
            return f"{bytes / (1024 ** 3):.1f} GB"
