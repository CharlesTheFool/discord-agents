"""
Attachment type classifier for Discord attachments.
Categorizes files by extension and provides utility methods for file type handling.
"""

import mimetypes
from pathlib import Path
from typing import List, Set, Dict


class AttachmentClassifier:
    """Static utility class for classifying file attachments by type"""

    # Extension to category mappings
    EXTENSION_CATEGORIES: Dict[str, Set[str]] = {
        'image': {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'},
        'document': {'.pdf', '.docx', '.doc', '.txt', '.md', '.rtf', '.odt'},
        'spreadsheet': {'.xlsx', '.xls', '.csv', '.ods'},
        'code': {'.py', '.js', '.ts', '.java', '.cpp', '.c', '.rs', '.go', '.rb',
                 '.json', '.yaml', '.yml', '.xml', '.html', '.css', '.php', '.sh'},
        'archive': {'.zip', '.tar', '.gz', '.rar', '.7z'},
    }

    @staticmethod
    def get_extension(filename: str) -> str:
        """
        Extract lowercase extension from filename without dot.

        Args:
            filename: Name of the file

        Returns:
            Extension without dot (e.g., "csv" from "data.CSV")
        """
        path = Path(filename)
        ext = path.suffix.lower()
        return ext[1:] if ext else ''

    @staticmethod
    def classify(filename: str) -> str:
        """
        Classify file into category based on extension.

        Args:
            filename: Name of the file

        Returns:
            Category string ('image', 'document', 'spreadsheet', 'code', 'archive', 'other')
        """
        path = Path(filename)

        # Handle double extensions like .tar.gz
        suffixes = [s.lower() for s in path.suffixes]

        # Check each suffix against categories
        for suffix in reversed(suffixes):
            for category, extensions in AttachmentClassifier.EXTENSION_CATEGORIES.items():
                if suffix in extensions:
                    return category

        return 'other'

    @staticmethod
    def is_supported(filename: str, supported_types: List[str]) -> bool:
        """
        Check if file type is in list of supported types.

        Args:
            filename: Name of the file
            supported_types: List of supported category strings

        Returns:
            True if file's category is in supported_types
        """
        category = AttachmentClassifier.classify(filename)
        return category in supported_types

    @staticmethod
    def guess_mime_type(filename: str) -> str:
        """
        Guess MIME type based on file extension.

        Args:
            filename: Name of the file

        Returns:
            MIME type string (e.g., "text/csv")
        """
        mime_type, _ = mimetypes.guess_type(filename)
        return mime_type or 'application/octet-stream'

    @staticmethod
    def is_document_block_eligible(filename: str) -> bool:
        """
        Check if file can be added as a document content block in Messages API.

        Messages API only accepts PDF and plaintext documents for document blocks.
        Other files (CSV, Excel, DOCX, code) should be uploaded to Files API
        but accessed via code execution tool instead.

        Args:
            filename: Name of the file

        Returns:
            True if file can be used as document content block
        """
        ext = AttachmentClassifier.get_extension(filename).lower()

        # Document block eligible: PDF and plaintext files
        # These can be directly viewed by Claude in conversation
        plaintext_extensions = {
            'txt', 'md', 'csv', 'json', 'xml', 'html', 'yaml', 'yml',
            'py', 'js', 'ts', 'java', 'cpp', 'c', 'rs', 'go', 'rb',
            'php', 'sh', 'css', 'rtf'
        }

        return ext == 'pdf' or ext in plaintext_extensions

    @staticmethod
    def is_plaintext_native(filename: str) -> bool:
        """
        Check if file is plaintext that Claude API can process natively.

        These files can be:
        - Embedded inline if <100k tokens (v0.5.0 Phase 3)
        - Uploaded to Files API if ≥100k tokens (use code execution)

        Binary/exotic files (xlsx, docx, pptx) are NOT plaintext native
        and should always use Files API + code execution regardless of size.

        Args:
            filename: Name of the file

        Returns:
            True if file is plaintext natively processable by Claude API
        """
        ext = AttachmentClassifier.get_extension(filename).lower()

        # Plaintext extensions Claude can process natively
        plaintext_extensions = {
            'txt', 'md', 'markdown', 'csv', 'json', 'xml', 'html', 'htm',
            'yaml', 'yml', 'ini', 'cfg', 'conf', 'log', 'rtf',
            # Code files
            'py', 'js', 'ts', 'jsx', 'tsx', 'java', 'cpp', 'c', 'h', 'hpp',
            'rs', 'go', 'rb', 'php', 'sh', 'bash', 'zsh', 'fish',
            'css', 'scss', 'sass', 'less', 'sql', 'r', 'swift', 'kt', 'scala'
        }

        # PDF is also natively processable
        return ext == 'pdf' or ext in plaintext_extensions

    @staticmethod
    def get_files_api_mime_type(filename: str) -> str:
        """
        Get MIME type optimized for Anthropic Files API compatibility.

        Files API accepts various MIME types, but Messages API only accepts
        text/plain and application/pdf for document content blocks.
        For plaintext files, we use text/plain to ensure Messages API compatibility.

        Args:
            filename: Name of the file

        Returns:
            MIME type string suitable for Files API
        """
        ext = AttachmentClassifier.get_extension(filename).lower()

        # For document block eligible files, use Messages API compatible MIME types
        if ext == 'pdf':
            return 'application/pdf'

        # All plaintext files use text/plain for Messages API compatibility
        plaintext_extensions = {
            'txt', 'md', 'csv', 'json', 'xml', 'html', 'yaml', 'yml',
            'py', 'js', 'ts', 'java', 'cpp', 'c', 'rs', 'go', 'rb',
            'php', 'sh', 'css', 'rtf'
        }

        if ext in plaintext_extensions:
            return 'text/plain'

        # For other file types (Excel, DOCX, images, archives), use standard guess
        return AttachmentClassifier.guess_mime_type(filename)
