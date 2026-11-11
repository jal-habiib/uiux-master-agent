import argparse
import logging
import sys
from pathlib import Path
from agent import run

# Set up logging so you can see what the agent is thinking
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

def main():
    parser = argparse.ArgumentParser(description="uiux-agent — hybrid UI/UX expert")
    parser.add_argument("input", nargs="+", help="Text prompt and/or image path")
    args = parser.parse_args()

    tokens = args.input
    image_paths = []
    text_tokens = []
    
    # Separate text from image paths
    for t in tokens:
        p = Path(t)
        if p.exists() and p.suffix.lower() in IMAGE_EXTS:
            image_paths.append(str(p))
        else:
            text_tokens.append(t)

    prompt = " ".join(text_tokens) if text_tokens else "Critique this UI."
    image_label = f"[Images: {', '.join(image_paths)}]" if image_paths else ""
    print("\n" + "─" * 60)
    print(f"USER: {prompt} {image_label}")
    print("─" * 60 + "\n")
    
    # Run the agent!
    result = run(prompt, image_paths=image_paths)
    
    print("\n" + "─" * 60)
    print("AGENT RESPONSE:")
    print("─" * 60)
    print(result)
    print("─" * 60)

if __name__ == "__main__":
    main()