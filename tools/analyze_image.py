import base64
from pathlib import Path

from openai import OpenAI

from config import API_KEY, BASE_URL, MODEL, ROOT


ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
MAX_IMAGE_BYTES = 15 * 1024 * 1024
UPLOADS_DIR = ROOT / "temp_uploads"


def _validated_image_path(image_path: str) -> Path:
    if not isinstance(image_path, str) or not image_path.strip():
        raise ValueError("A valid uploaded image path is required.")

    uploads_root = UPLOADS_DIR.resolve()
    candidate = Path(image_path).expanduser().resolve()

    if not candidate.is_relative_to(uploads_root):
        raise ValueError("Image analysis is limited to files in the temp_uploads directory.")

    if candidate.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError("Only PNG and JPEG images can be analyzed.")

    if not candidate.is_file():
        raise ValueError("Uploaded image file was not found.")

    if candidate.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds the 15 MB analysis limit.")

    return candidate


def analyze_image(image_path: str) -> dict:
    try:
        path = _validated_image_path(image_path)
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }[path.suffix.lower()]
        image_data = base64.b64encode(path.read_bytes()).decode("ascii")
        client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Describe this UI screenshot in structured detail. Output JSON with keys: "
                                "layout (overall structure), sections (ordered list of visual blocks), "
                                "palette (hex colors you can identify), typography (weights/sizes you see), "
                                "components (buttons, cards, inputs), issues (anything visually off)."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{image_data}"},
                        },
                    ],
                }
            ],
        )
        return {"analysis": response.choices[0].message.content}
    except Exception as exc:
        return {"error": f"Unable to analyze image: {exc}"}
