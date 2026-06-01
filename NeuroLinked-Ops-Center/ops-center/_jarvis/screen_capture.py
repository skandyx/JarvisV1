"""
Jarvis V2 — Screen Capture
Takes screenshots and describes them via Claude Vision.
"""

import base64
import io

try:
    from PIL import ImageGrab
    _GRAB_OK = True
except Exception:
    _GRAB_OK = False


def capture_screen() -> bytes:
    """Capture the entire screen, return PNG bytes."""
    if not _GRAB_OK:
        raise RuntimeError("PIL.ImageGrab non disponible (Linux sans display ou Pillow manquant)")
    img = ImageGrab.grab()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def describe_screen(anthropic_client) -> str:
    """Capture screen and describe it using Claude Vision."""
    png_bytes = capture_screen()
    b64 = base64.b64encode(png_bytes).decode("utf-8")

    response = await anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64,
                    },
                },
                {
                    "type": "text",
                    "text": "You are JARVIS, a professional AI assistant. Describe what is visible on this screen in a crisp, clear English butler tone. Two to three sentences maximum. Name the main open program and briefly what the user appears to be working on. No fluff, no commentary, no jokes. Just the information.",
                },
            ],
        }],
    )
    return response.content[0].text


async def describe_webcam_frame(anthropic_client, jpeg_b64: str, prompt: str = None) -> str:
    """Describe what Jarvis sees through the webcam. Takes a pre-captured JPEG base64 string."""
    if not jpeg_b64:
        return "No webcam frame available — camera may not be connected."

    if prompt is None:
        prompt = (
            "You are JARVIS looking at your user through a webcam. "
            "Describe what you see: the person, their expression, their environment, "
            "anything notable they're holding or doing. Two to three sentences maximum. "
            "Be natural and conversational, as if you're a butler observing your employer. "
            "If the image is blurry or dark, say so briefly."
        )

    response = await anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": jpeg_b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return response.content[0].text
