def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i+2], 16) / 255 for i in (0, 2, 4))
    def f(c): return c/12.92 if c <= 0.03928 else ((c+0.055)/1.055) ** 2.4
    r, g, b = f(r), f(g), f(b)
    return 0.2126*r + 0.7152*g + 0.0722*b

def check_contrast(fg: str, bg: str) -> dict:
    try:
        l1, l2 = _lum(fg), _lum(bg)
        ratio = (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)
        return {
            "ratio": round(ratio, 2),
            "AA_normal": ratio >= 4.5,
            "AA_large": ratio >= 3.0,
            "AAA_normal": ratio >= 7.0,
        }
    except Exception as e:
        return {"error": f"Could not parse colors: {e}"}