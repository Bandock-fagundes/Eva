"""Gera o arquivo de versão do Windows (Propriedades > Detalhes do EVA-Agente.exe) a partir de eva_agente.VERSAO.

Uso (na pasta agente_windows):  python empacotar/gerar_versao.py  [tag]
Se a tag (ex.: agente-v1.0.0) for passada, confere se bate com VERSAO e falha se não bater.
"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from eva_agente import VERSAO  # noqa: E402

tag = sys.argv[1] if len(sys.argv) > 1 else ""
if tag.startswith("refs/tags/"):
    tag = tag[len("refs/tags/"):]
if tag.startswith("agente-v") and tag != f"agente-v{VERSAO}":
    sys.exit(f"ERRO: a tag {tag} não bate com VERSAO={VERSAO} em eva_agente/__init__.py")

n = [int(x) for x in (VERSAO.split(".") + ["0", "0", "0"])[:3]] + [0]
tupla = ", ".join(str(x) for x in n)
texto = f"""# gerado por empacotar/gerar_versao.py
VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tupla}), prodvers=({tupla}), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041604B0', [
      StringStruct('CompanyName', 'E.V.A. do Gabriel'),
      StringStruct('FileDescription', 'EVA-Agente - deixa a Eva usar este PC com segurança'),
      StringStruct('FileVersion', '{VERSAO}'),
      StringStruct('InternalName', 'EVA-Agente'),
      StringStruct('LegalCopyright', 'Uso pessoal'),
      StringStruct('OriginalFilename', 'EVA-Agente.exe'),
      StringStruct('ProductName', 'EVA-Agente'),
      StringStruct('ProductVersion', '{VERSAO}')])]),
    VarFileInfo([VarStruct('Translation', [0x0416, 1200])])
  ]
)
"""
(RAIZ / "empacotar" / "versao_windows.txt").write_text(texto, encoding="utf-8")
print(f"versao_windows.txt gerado para {VERSAO}")
