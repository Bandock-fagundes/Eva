"""Processamento dos prints: desenha o cursor, reduz e comprime em JPEG."""
from __future__ import annotations

import hashlib
import io


def desenhar_cursor(img, x: int, y: int):
    from PIL import ImageDraw
    w, h = img.size
    if not (0 <= x < w and 0 <= y < h):
        return
    esc = max(1.0, w / 1600)  # telas grandes: cursor proporcional
    pts = [(0, 0), (0, 18), (5, 14), (9, 22), (12, 20), (8, 13), (14, 13)]
    seta = [(x + px * esc, y + py * esc) for px, py in pts]
    d = ImageDraw.Draw(img)
    d.polygon(seta, fill=(255, 255, 255), outline=(0, 0, 0))


def processar(img, cursor: tuple[int, int] | None, largura: int | None, qualidade: int) -> tuple[bytes, int, int, int, int]:
    """img: PIL.Image (tela inteira, pixels reais). Devolve (jpeg, w_real, h_real, w_img, h_img)."""
    from PIL import Image
    if img.mode != "RGB":
        img = img.convert("RGB")
    w0, h0 = img.size
    if cursor is not None:
        img = img.copy()
        desenhar_cursor(img, int(cursor[0]), int(cursor[1]))
    fator = 1.0
    if largura and largura < w0:
        fator = max(0.1, largura / w0)
    if fator < 0.999:
        img = img.resize((max(1, round(w0 * fator)), max(1, round(h0 * fator))), Image.LANCZOS)
    qualidade = max(10, min(95, int(qualidade)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=qualidade, optimize=False)
    return buf.getvalue(), w0, h0, img.size[0], img.size[1]


def impressao_digital(img) -> str:
    """Resumo barato da imagem para não reenviar quadros iguais na tela ao vivo."""
    pequeno = img.convert("L").resize((64, 36))
    return hashlib.blake2b(pequeno.tobytes(), digest_size=12).hexdigest()
