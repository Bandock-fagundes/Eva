"""Windows DPAPI (CryptProtectData): cifra o token com a conta do Windows do Gabriel.
Só o mesmo usuário, no mesmo PC, consegue decifrar. Sem pywin32: só ctypes."""
from __future__ import annotations

import ctypes
import sys

ENTROPIA = b"EVA-Agente/token/v1"


def disponivel() -> bool:
    return sys.platform == "win32"


if sys.platform == "win32":
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_BLOB),
                                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.POINTER(_BLOB),
                                            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    def _blob(dados: bytes) -> tuple[_BLOB, object]:
        buf = ctypes.create_string_buffer(dados, len(dados))
        return _BLOB(len(dados), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _ler(b: _BLOB) -> bytes:
        try:
            return ctypes.string_at(b.pbData, b.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(b.pbData, ctypes.c_void_p))

    def cifrar(dados: bytes) -> bytes:
        entrada, _b1 = _blob(dados)
        ent, _b2 = _blob(ENTROPIA)
        saida = _BLOB()
        if not _crypt32.CryptProtectData(ctypes.byref(entrada), "EVA-Agente", ctypes.byref(ent), None, None, 0x1,
                                         ctypes.byref(saida)):
            raise OSError(ctypes.get_last_error(), "CryptProtectData falhou")
        return _ler(saida)

    def decifrar(dados: bytes) -> bytes:
        entrada, _b1 = _blob(dados)
        ent, _b2 = _blob(ENTROPIA)
        saida = _BLOB()
        if not _crypt32.CryptUnprotectData(ctypes.byref(entrada), None, ctypes.byref(ent), None, None, 0x1,
                                           ctypes.byref(saida)):
            raise OSError(ctypes.get_last_error(), "CryptUnprotectData falhou")
        return _ler(saida)
else:
    def cifrar(dados: bytes) -> bytes:
        raise OSError("DPAPI só existe no Windows")

    def decifrar(dados: bytes) -> bytes:
        raise OSError("DPAPI só existe no Windows")
