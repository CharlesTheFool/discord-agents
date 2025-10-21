"""
Image Processing Pipeline for Claude API

Multi-strategy compression to fit within 5MB API limit.
Target: 73% of limit (~3.65MB) to account for Base64 overhead.
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
    Why 73%: Base64 encoding inflates size by 4/3 (33% overhead)

    Strategy sequence (each attempts to reach target):
    1. Check if compression needed
    2. Optimize current format
    3. JPEG quality reduction (85→75→65→...→10)
    4. WebP conversion (85→75→65→...→15)
    5. Nuclear resize (0.7x dimensions)
    6. Thumbnail fallback (512x512)
    """

    def __init__(self):
        self.api_limit = 5 * 1024 * 1024  # 5MB per Claude API
        self.target_size = int(self.api_limit * 0.73)  # Account for Base64 overhead

        # Security limits for downloads
        self.max_download_size = 50 * 1024 * 1024  # 50MB
        self.download_timeout = 30

        # Whitelist Discord CDN domains only
        self.allowed_domains = [
            "cdn.discordapp.com",
            "media.discordapp.net"
        ]

    async def process_attachment(self, attachment) -> Optional[Dict]:
        """
        Process Discord image attachment into Claude API format.

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
        # Security: Only allow Discord CDN URLs
        if not self._is_allowed_url(attachment.url):
            logger.warning(f"Blocked non-Discord URL: {attachment.url}")
            return None

        try:
            image_data = await self._download_image(attachment.url)
        except Exception as e:
            logger.error(f"Failed to download image: {e}")
            return None

        # Skip compression if already small enough
        if not self._needs_compression(image_data, attachment.size):
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": self._guess_mime_type(attachment.filename),
                    "data": base64.b64encode(image_data).decode()
                }
            }

        # Run compression pipeline
        compressed = await self._compress_image(image_data)

        if compressed is None:
            logger.error("Compression failed")
            return None

        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/webp",  # Most aggressive strategies use WebP
                "data": base64.b64encode(compressed).decode()
            }
        }

    def _is_allowed_url(self, url: str) -> bool:
        """Verify URL is from Discord CDN (prevent arbitrary downloads)"""
        parsed = urlparse(url)
        return parsed.netloc in self.allowed_domains

    async def _download_image(self, url: str) -> bytes:
        """
        Download image with size and timeout limits.

        Raises:
            Exception if download fails or exceeds limits
        """
        timeout = aiohttp.ClientTimeout(total=self.download_timeout)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}")

                # Stream download in chunks, abort if too large
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
        Check if compression needed based on actual size.

        Note: Uses len(image_data) instead of reported_size because
        Discord's reported size can be inaccurate.
        """
        return len(image_data) > self.target_size

    async def _compress_image(self, image_data: bytes) -> Optional[bytes]:
        """
        Apply compression strategies sequentially until target met.

        Each strategy returns early if target is reached.
        """
        try:
            img = Image.open(BytesIO(image_data))
        except Exception as e:
            logger.error(f"Failed to open image with PIL: {e}")
            return None

        original_format = img.format

        # Strategy 1: Optimize current format (lossless)
        result = self._optimize_format(img)
        if len(result) <= self.target_size:
            logger.info(f"Compressed via optimization: {len(result)} bytes")
            return result

        # Strategy 2: JPEG quality reduction (only for JPEGs)
        if original_format in ['JPEG', 'JPG']:
            result = self._try_jpeg_quality(img)
            if result and len(result) <= self.target_size:
                logger.info(f"Compressed via JPEG quality: {len(result)} bytes")
                return result

        # Strategy 3: WebP conversion (better compression than JPEG)
        result = self._try_webp_conversion(img)
        if result and len(result) <= self.target_size:
            logger.info(f"Compressed via WebP: {len(result)} bytes")
            return result

        # Strategy 4: Nuclear resize (reduce dimensions)
        result = self._try_nuclear_resize(img)
        if result and len(result) <= self.target_size:
            logger.info(f"Compressed via nuclear resize: {len(result)} bytes")
            return result

        # Strategy 5: Thumbnail fallback (always succeeds)
        result = self._try_thumbnail_fallback(img)
        logger.info(f"Compressed via thumbnail fallback: {len(result)} bytes")
        return result

    def _optimize_format(self, img: Image.Image) -> bytes:
        """
        Optimize image in current format using PIL's optimize flag.
        Lossless but limited effectiveness.
        """
        buffer = BytesIO()

        # Convert RGBA→RGB if saving as JPEG (JPEG doesn't support transparency)
        if img.mode == 'RGBA' and img.format == 'JPEG':
            img = img.convert('RGB')

        img.save(buffer, format=img.format or 'PNG', optimize=True)
        return buffer.getvalue()

    def _try_jpeg_quality(self, img: Image.Image) -> Optional[bytes]:
        """
        Reduce JPEG quality progressively: 85→75→65→...→10
        Returns first result that meets target.
        """
        if img.mode == 'RGBA':
            img = img.convert('RGB')  # Strip transparency for JPEG

        qualities = [85, 75, 65, 55, 45, 35, 25, 15, 10]

        for quality in qualities:
            buffer = BytesIO()
            img.save(buffer, format='JPEG', quality=quality, optimize=True)
            result = buffer.getvalue()

            if len(result) <= self.target_size:
                return result

        return None  # None of the quality levels worked

    def _try_webp_conversion(self, img: Image.Image) -> Optional[bytes]:
        """
        Convert to WebP with progressive quality reduction: 85→75→...→15
        WebP typically compresses 25-35% better than JPEG.
        """
        qualities = [85, 75, 65, 55, 45, 35, 25, 15]

        for quality in qualities:
            buffer = BytesIO()
            # method=6: slowest but best compression
            img.save(buffer, format='WEBP', quality=quality, method=6)
            result = buffer.getvalue()

            if len(result) <= self.target_size:
                return result

        return None

    def _try_nuclear_resize(self, img: Image.Image) -> Optional[bytes]:
        """
        Resize to 70% of original dimensions.
        Preserves aspect ratio but reduces resolution.
        """
        new_width = int(img.width * 0.7)
        new_height = int(img.height * 0.7)

        resized = img.resize(
            (new_width, new_height),
            Image.Resampling.LANCZOS  # High-quality downsampling filter
        )

        buffer = BytesIO()
        resized.save(buffer, format='WEBP', quality=75, method=6)
        return buffer.getvalue()

    def _try_thumbnail_fallback(self, img: Image.Image) -> bytes:
        """
        Last resort: Thumbnail to 512x512.
        Maintains aspect ratio, always succeeds.
        """
        img.thumbnail((512, 512), Image.Resampling.LANCZOS)

        buffer = BytesIO()
        img.save(buffer, format='WEBP', quality=85, method=6)
        return buffer.getvalue()

    def _guess_mime_type(self, filename: str) -> str:
        """Map file extension to MIME type"""
        ext = filename.lower().split('.')[-1]

        mime_types = {
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'gif': 'image/gif',
            'webp': 'image/webp'
        }

        return mime_types.get(ext, 'image/jpeg')
