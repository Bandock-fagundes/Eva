"""Nomes de teclas: aceita nomes amigáveis (enter, esc, ctrl), nomes do X/xdotool (Return, BackSpace, Prior, super)
e em português (apagar, espaco, cima). Tudo vira um nome canônico que cada plataforma traduz (Windows: códigos VK).

    normalizar("ctrl+a BackSpace") -> [["ctrl", "a"], ["backspace"]]
"""
from __future__ import annotations

import re

MODIFICADORES = ("ctrl", "alt", "shift", "win")

ALIASES = {
    "control": "ctrl", "ctl": "ctrl", "controle": "ctrl", "lctrl": "ctrl", "rctrl": "ctrl",
    "menu_alt": "alt", "altgr": "alt", "lalt": "alt", "ralt": "alt",
    "lshift": "shift", "rshift": "shift",
    "super": "win", "windows": "win", "meta": "win", "cmd": "win", "lwin": "win", "rwin": "win", "super_l": "win",
    "enter": "enter", "return": "enter", "kp_enter": "enter", "intro": "enter",
    "esc": "esc", "escape": "esc",
    "tab": "tab",
    "backspace": "backspace", "back_space": "backspace", "apagar": "backspace", "bksp": "backspace",
    "delete": "delete", "del": "delete", "excluir": "delete",
    "space": "space", "espaco": "space", "espaço": "space", "barra": "space",
    "up": "up", "cima": "up", "down": "down", "baixo": "down",
    "left": "left", "esquerda": "left", "right": "right", "direita": "right",
    "pageup": "pageup", "pgup": "pageup", "prior": "pageup", "page_up": "pageup",
    "pagedown": "pagedown", "pgdn": "pagedown", "next": "pagedown", "page_down": "pagedown",
    "home": "home", "inicio": "home", "início": "home", "end": "end", "fim": "end",
    "insert": "insert", "ins": "insert",
    "printscreen": "printscreen", "print": "printscreen", "prtsc": "printscreen",
    "menu": "apps", "apps": "apps", "contextmenu": "apps",
    "capslock": "capslock", "caps_lock": "capslock", "numlock": "numlock", "scrolllock": "scrolllock",
    "pause": "pause",
    "volumeup": "volumeup", "volumedown": "volumedown", "volumemute": "volumemute", "mute": "volumemute",
    "playpause": "playpause", "mediaplaypause": "playpause", "medianext": "medianext", "mediaprev": "mediaprev",
}
for _nome in list(ALIASES.values()) + list(MODIFICADORES):
    ALIASES.setdefault(_nome, _nome)
NOMEADAS = set(ALIASES.values()) | {f"f{i}" for i in range(1, 25)}
MAX_COMBOS = 30


class TeclaInvalida(ValueError):
    pass


def canonica(nome: str) -> str:
    p = nome.strip()
    if not p:
        raise TeclaInvalida("tecla vazia")
    low = p.lower()
    if low in ALIASES:
        return ALIASES[low]
    if re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", low):
        return low
    if len(p) == 1:
        return p.lower() if p.isalpha() else p
    raise TeclaInvalida(f"tecla desconhecida: {nome!r}")


def normalizar(teclas) -> list[list[str]]:
    """'ctrl+l' -> [['ctrl','l']];  'ctrl+a Delete' -> [['ctrl','a'], ['delete']];  lista também vale."""
    if isinstance(teclas, (list, tuple)):
        combos = [str(c) for c in teclas]
    else:
        combos = str(teclas or "").replace(",", " ").split()
    if len(combos) > MAX_COMBOS:
        raise TeclaInvalida(f"teclas demais de uma vez (máx. {MAX_COMBOS})")
    saida = []
    for combo in combos:
        if combo == "+":
            saida.append(["+"])
            continue
        partes = [canonica(x) for x in re.split(r"(?<!^)\+(?!$)", combo) if x != ""]
        if partes:
            saida.append(partes)
    if not saida:
        raise TeclaInvalida("nenhuma tecla informada")
    return saida
