import base64
from pathlib import Path

from openai import OpenAI

from config import API_KEY, BASE_URL, MODEL

def analyze_image(image_path: str) -> dict:
    p = Path(image_path)
    if not p.exists():
        return {"error": f"File not found: {image_path}"}

    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    image_data = base64.b64encode(p.read_bytes()).decode("ascii")
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