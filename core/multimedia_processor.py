"""
Multimedia Processor

Handles processing of non-image files (PDFs, Office documents, text files)
using Claude's code execution tool and Files API.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    import discord
    from anthropic import Anthropic

logger = logging.getLogger(__name__)


class MultimediaProcessor:
    """
    Process various file types using Claude's code execution tool.

    Responsibilities:
    - Detect file types from attachments
    - Upload files to Anthropic's Files API
    - Route to appropriate processing strategy
    - Extract text from PDFs, Office documents, etc.
    """

    SUPPORTED_TYPES = {
        # Text files
        'text': ['.txt', '.md', '.log', '.csv', '.json', '.xml', '.yaml', '.yml'],

        # PDFs
        'pdf': ['.pdf'],

        # Office documents
        'docx': ['.docx'],
        'xlsx': ['.xlsx', '.xls'],
        'pptx': ['.pptx'],

        # Code files
        'code': ['.py', '.js', '.ts', '.java', '.cpp', '.c', '.rs', '.go', '.rb']
    }

    def __init__(self, anthropic_client: 'Anthropic', max_file_size_mb: int = 32):
        """
        Initialize Multimedia Processor.

        Args:
            anthropic_client: Anthropic API client for file uploads
            max_file_size_mb: Maximum file size to process (MB)
        """
        self.anthropic_client = anthropic_client
        self.max_file_size = max_file_size_mb * 1024 * 1024  # Convert to bytes

        logger.info(f"MultimediaProcessor initialized (max size: {max_file_size_mb}MB)")

    def detect_file_type(self, filename: str) -> Optional[str]:
        """
        Detect file type from filename extension.

        Args:
            filename: Name of the file

        Returns:
            File type category ('text', 'pdf', 'docx', etc.) or None if unsupported
        """
        ext = Path(filename).suffix.lower()

        for file_type, extensions in self.SUPPORTED_TYPES.items():
            if ext in extensions:
                return file_type

        return None

    def is_supported(self, attachment: 'discord.Attachment') -> bool:
        """
        Check if attachment is a supported multimedia file.

        Args:
            attachment: Discord attachment object

        Returns:
            True if supported, False otherwise
        """
        # Check file size
        if attachment.size > self.max_file_size:
            logger.warning(f"File {attachment.filename} exceeds max size ({attachment.size} > {self.max_file_size})")
            return False

        # Check file type
        file_type = self.detect_file_type(attachment.filename)
        if not file_type:
            logger.debug(f"Unsupported file type: {attachment.filename}")
            return False

        return True

    async def process_attachment(self, attachment: 'discord.Attachment') -> Optional[Dict[str, Any]]:
        """
        Process a Discord attachment and prepare it for Claude.

        Args:
            attachment: Discord attachment object

        Returns:
            Dictionary with file information for Claude API, or None if processing fails:
            {
                "type": "container_upload",
                "file_id": "file_abc123",
                "filename": "document.pdf",
                "path": "/tmp/uploads/document.pdf",
                "file_type": "pdf"
            }
        """
        if not self.is_supported(attachment):
            logger.debug(f"Skipping unsupported file: {attachment.filename}")
            return None

        try:
            # Download file data
            file_data = await attachment.read()

            # Upload to Anthropic Files API
            file_response = self.anthropic_client.files.create(
                file=(attachment.filename, file_data),
                purpose="user_upload"
            )

            file_type = self.detect_file_type(attachment.filename)

            result = {
                "type": "container_upload",
                "file_id": file_response.id,
                "filename": attachment.filename,
                "path": f"/tmp/uploads/{attachment.filename}",
                "file_type": file_type,
                "size": attachment.size
            }

            logger.info(f"Processed multimedia file: {attachment.filename} (type: {file_type}, ID: {file_response.id})")

            return result

        except Exception as e:
            logger.error(f"Failed to process attachment {attachment.filename}: {e}", exc_info=True)
            return None

    def get_processing_instructions(self, file_type: str, filename: str) -> str:
        """
        Generate Claude instructions for processing a file type.

        Args:
            file_type: Type of file ('pdf', 'docx', etc.)
            filename: Name of the file

        Returns:
            Instructions string for Claude
        """
        instructions = {
            'pdf': f"""
The file '{filename}' is a PDF document available at /tmp/uploads/{filename}.
You can extract text using pypdf or pdfplumber, both pre-installed:

```python
import pypdf
# or
import pdfplumber
```
""",
            'docx': f"""
The file '{filename}' is a Word document available at /tmp/uploads/{filename}.
You can read it using python-docx (pre-installed):

```python
from docx import Document
doc = Document('/tmp/uploads/{filename}')
```
""",
            'xlsx': f"""
The file '{filename}' is an Excel spreadsheet available at /tmp/uploads/{filename}.
You can read it using pandas or openpyxl (both pre-installed):

```python
import pandas as pd
df = pd.read_excel('/tmp/uploads/{filename}')
# or
import openpyxl
wb = openpyxl.load_workbook('/tmp/uploads/{filename}')
```
""",
            'text': f"""
The file '{filename}' is a text file available at /tmp/uploads/{filename}.
You can read it directly:

```python
with open('/tmp/uploads/{filename}') as f:
    content = f.read()
```
""",
            'code': f"""
The file '{filename}' is a code file available at /tmp/uploads/{filename}.
You can read and analyze it directly.
"""
        }

        return instructions.get(file_type, f"File '{filename}' is available at /tmp/uploads/{filename}")
