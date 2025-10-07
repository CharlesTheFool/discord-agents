"""
Minimal test for image processor
"""

import pytest
from PIL import Image
from io import BytesIO
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tools.image_processor import ImageProcessor


def test_image_compression():
    """Test basic image compression"""
    processor = ImageProcessor()

    # Create a large test image (10MB uncompressed)
    img = Image.new('RGB', (2000, 2000), color='red')
    buffer = BytesIO()
    img.save(buffer, format='PNG')
    large_image_data = buffer.getvalue()

    # Should need compression
    assert processor._needs_compression(large_image_data, len(large_image_data))

    # Try to compress
    compressed = processor._compress_image(large_image_data)

    assert compressed is not None
    assert len(compressed) < len(large_image_data)
    assert len(compressed) <= processor.target_size

    print(f"✓ Image compression test passed")
    print(f"  Original: {len(large_image_data)} bytes")
    print(f"  Compressed: {len(compressed)} bytes")
    print(f"  Target: {processor.target_size} bytes")


def test_small_image_no_compression():
    """Test that small images don't get compressed"""
    processor = ImageProcessor()

    # Create a small test image
    img = Image.new('RGB', (100, 100), color='blue')
    buffer = BytesIO()
    img.save(buffer, format='PNG')
    small_image_data = buffer.getvalue()

    # Should not need compression
    assert not processor._needs_compression(small_image_data, len(small_image_data))

    print(f"✓ Small image test passed")
    print(f"  Size: {len(small_image_data)} bytes (no compression needed)")


def test_url_validation():
    """Test that only Discord CDN URLs are allowed"""
    processor = ImageProcessor()

    # Valid Discord URLs
    assert processor._is_allowed_url("https://cdn.discordapp.com/attachments/123/456/image.png")
    assert processor._is_allowed_url("https://media.discordapp.net/attachments/123/456/image.jpg")

    # Invalid URLs
    assert not processor._is_allowed_url("https://example.com/image.png")
    assert not processor._is_allowed_url("https://malicious.com/image.png")

    print(f"✓ URL validation test passed")


if __name__ == "__main__":
    test_image_compression()
    test_small_image_no_compression()
    test_url_validation()
    print("\n✅ All image processor tests passed!")
