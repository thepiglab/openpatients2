import httpx
import pytest
from openpatients2.vision import fetch_pixels

URL='https://pmc-oa-opendata.s3.amazonaws.com/PMC1.1/figure.png'

async def test_pixels_are_bounded_and_magic_checked_without_files():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'\x89PNG\r\n\x1a\nabcdef'))) as client:
        encoded,provenance=await fetch_pixels(URL,http=client)
        assert encoded.startswith('data:image/png;base64,')
        assert not provenance['persisted_pixels'] and provenance['bytes']==14
        with pytest.raises(ValueError,match='byte cap'):await fetch_pixels(URL,max_bytes=5,http=client)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'<html>not image</html>'))) as client:
        with pytest.raises(ValueError,match='Unsupported'):await fetch_pixels(URL,http=client)

async def test_untrusted_media_host_rejected_before_request():
    with pytest.raises(ValueError):await fetch_pixels('https://example.com/a.jpg')
