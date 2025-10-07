"""
Image Processing Pipeline for Claude API

Multi-strategy compression to fit within 5MB API limit.
Target: 73% of limit (~3.65MB) to account for Base64 overhead.

Ported from preserved_algorithms.md
"""

from PIL import Image
from io import BytesIO
import aiohttp
import base64
import logging
from typing import Optional, Dict
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class ImageProcessor:
    """
    Multi-strategy image compression pipeline.

    Target: 73% of 5MB API limit = ~3.65MB
    Why 73%: Base64 encoding adds ~33% overhead (4/3)

    Strategy sequence:
    1. Check if compression needed
    2. Optimize current format
    3. JPEG quality reduction (85→75→65→...→10)
    4. WebP conversion (85→75→65→...→15)
    5. Nuclear resize (0.7x dimensions)
    6. Thumbnail fallback (512x512)
    """

    def __init__(self):
        # API limit per image
        self.api_limit = 5 * 1024 * 1024

        # Target: 73% of limit (accounts for Base64)
        self.target_size = int(self.api_limit * 0.73)

        # Security: Download limits
        self.max_download_size = 50 * 1024 * 1024  # 50MB
        self.download_timeout = 30  # seconds

        # Allowed CDN domains
        self.allowed_domains = [
            "cdn.discordapp.com",
            "media.discordapp.net"
        ]

    async def process_attachment(self, attachment) -> Optional[Dict]:
        """
        Process Discord image attachment.

        Args:
            attachment: discord.Attachment object

        Returns:
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg|png|webp",
                    "data": "base64_encoded_string"
                }
            }

            None if processing fails
        """
        # Security check
        if not self._is_allowed_url(attachment.url):
            logger.warning(f"Blocked non-Discord URL: {attachment.url}")
            return None

        # Download image
        try:
            image_data = await self._download_image(attachment.url)
        except Exception as e:
            logger.error(f"Failed to download image: {e}")
            return None

        # Check if compression needed
        if not self._needs_compression(image_data, attachment.size):
            # Small enough, use as-is
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": self._guess_mime_type(attachment.filename),
                    "data": base64.b64encode(image_data).decode()
                }
            }

        # Compress image
        compressed = await self._compress_image(image_data)

        if compressed is None:
            logger.error("Compression failed")
            return None

        # Return Claude API format
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/webp",  # Most compressed use WebP
                "data": base64.b64encode(compressed).decode()
            }
        }

    def _is_allowed_url(self, url: str) -> bool:
        """Only allow Discord CDN URLs"""
        parsed = urlparse(url)
        return parsed.netloc in self.allowed_domains

    async def _download_image(self, url: str) -> bytes:
        """
        Securely download image with size/time limits.

        Raises:
            Exception if download fails or exceeds limits
        """
        timeout = aiohttp.ClientTimeout(total=self.download_timeout)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}")

                # Stream download, check size incrementally
                chunks = []
                total_size = 0

                async for chunk in resp.content.iter_chunked(1024 * 1024):  # 1MB chunks
                    chunks.append(chunk)
                    total_size += len(chunk)

                    if total_size > self.max_download_size:
                        raise Exception("Image too large (>50MB)")

                return b"".join(chunks)

    def _needs_compression(self, image_data: bytes, reported_size: int) -> bool:
        """
        Check if image needs compression.

        Args:
            image_data: Raw image bytes
            reported_size: Size reported by Discord

        Returns:
            True if compression needed, False otherwise
        """
        actual_size = len(image_data)

        # Use actual size, not reported (reported can be wrong)
        return actual_size > self.target_size

    async def _compress_image(self, image_data: bytes) -> Optional[bytes]:
        """
        Apply compression strategies sequentially until target met.

        Strategy order:
        1. Optimize current format
        2. JPEG quality reduction
        3. WebP conversion
        4. Nuclear resize
        5. Thumbnail fallback
        """
        try:
            img = Image.open(BytesIO(image_data))
        except Exception as e:
            logger.error(f"Failed to open image with PIL: {e}")
            return None

        # Store original format
        original_format = img.format

        # Strategy 1: Optimize current format
        result = self._optimize_format(img)
        if len(result) <= self.target_size:
            logger.info(f"Compressed via optimization: {len(result)} bytes")
            return result

        # Strategy 2: JPEG quality reduction (if JPEG/JPG)
        if original_format in ['JPEG', 'JPG']:
            result = self._try_jpeg_quality(img)
            if result and len(result) <= self.target_size:
                logger.info(f"Compressed via JPEG quality: {len(result)} bytes")
                return result

        # Strategy 3: WebP conversion
        result = self._try_webp_conversion(img)
        if result and len(result) <= self.target_size:
            logger.info(f"Compressed via WebP: {len(result)} bytes")
            return result

        # Strategy 4: Nuclear resize (0.7x dimensions)
        result = self._try_nuclear_resize(img)
        if result and len(result) <= self.target_size:
            logger.info(f"Compressed via nuclear resize: {len(result)} bytes")
            return result

        # Strategy 5: Thumbnail fallback (512x512)
        result = self._try_thumbnail_fallback(img)
        logger.info(f"Compressed via thumbnail fallback: {len(result)} bytes")
        return result

    def _optimize_format(self, img: Image.Image) -> bytes:
        """
        Optimize image in current format.
        Uses PIL's optimize parameter.
        """
        buffer = BytesIO()

        # Convert RGBA to RGB if saving as JPEG
        if img.mode == 'RGBA' and img.format == 'JPEG':
            img = img.convert('RGB')

        img.save(buffer, format=img.format or 'PNG', optimize=True)
        return buffer.getvalue()

    def _try_jpeg_quality(self, img: Image.Image) -> Optional[bytes]:
        """
        Try JPEG quality reduction: 85→75→65→55→45→35→25→15→10
        """
        # Convert RGBA to RGB for JPEG
        if img.mode == 'RGBA':
            img = img.convert('RGB')

        qualities = [85, 75, 65, 55, 45, 35, 25, 15, 10]

        for quality in qualities:
            buffer = BytesIO()
            img.save(buffer, format='JPEG', quality=quality, optimize=True)
            result = buffer.getvalue()

            if len(result) <= self.target_size:
                return result

        return None

    def _try_webp_conversion(self, img: Image.Image) -> Optional[bytes]:
        """
        Try WebP conversion: 85→75→65→55→45→35→25→15
        WebP generally compresses better than JPEG.
        """
        qualities = [85, 75, 65, 55, 45, 35, 25, 15]

        for quality in qualities:
            buffer = BytesIO()
            img.save(buffer, format='WEBP', quality=quality, method=6)
            result = buffer.getvalue()

            if len(result) <= self.target_size:
                return result

        return None

    def _try_nuclear_resize(self, img: Image.Image) -> Optional[bytes]:
        """
        Nuclear option: Resize to 70% dimensions.
        Reduces resolution but preserves aspect ratio.
        """
        new_width = int(img.width * 0.7)
        new_height = int(img.height * 0.7)

        resized = img.resize(
            (new_width, new_height),
            Image.Resampling.LANCZOS  # High-quality downsampling
        )

        # Save as WebP with good quality
        buffer = BytesIO()
        resized.save(buffer, format='WEBP', quality=75, method=6)
        return buffer.getvalue()

    def _try_thumbnail_fallback(self, img: Image.Image) -> bytes:
        """
        Last resort: Create 512x512 thumbnail.
        Maintains aspect ratio, fits within 512x512 box.
        """
        img.thumbnail((512, 512), Image.Resampling.LANCZOS)

        buffer = BytesIO()
        img.save(buffer, format='WEBP', quality=85, method=6)
        return buffer.getvalue()

    def _guess_mime_type(self, filename: str) -> str:
        """Guess MIME type from filename"""
        ext = filename.lower().split('.')[-1]

        mime_types = {
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'gif': 'image/gif',
            'webp': 'image/webp'
        }

        return mime_types.get(ext, 'image/jpeg')
