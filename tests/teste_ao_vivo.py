"""Teste AO VIVO contra o servidor real da Eva (usa o Gemini de verdade).

Uso:
    python tests/teste_ao_vivo.py https://sua-eva.onrender.com SUA_SENHA [--time Avaí]

Faz login e manda, via /api/chat, uma sequência de mensagens; imprime as respostas.
A senha não é impressa. Aprovações pedidas durante o teste são negadas automaticamente.
"""
import argparse
import sys
import time

import httpx


def main():
    ap = argparse.ArgumentParser(description="Teste ao vivo da E.V.A.")
    ap.add_argument("url")
    ap.add_argument("senha")
    ap.add_argument("--time", default="Avaí", help="time usado no teste de memória (padrão: Avaí)")
    ap.add_argument("--timeout", type=float, default=300)
    a = ap.parse_args()
    base = a.url.rstrip("/")

    # Render grátis "dorme": o primeiro acesso pode levar ~1 min para acordar
    with httpx.Client(base_url=base, timeout=a.timeout + 30, follow_redirects=False) as c:
        print(f"→ Acordando {base} …")
        for tentativa in range(12):
            try:
                r = c.get("/api/saude")
                if r.status_code == 200:
                    print("  saúde:", r.json())
                    break
            except httpx.HTTPError as e:
                print(f"  ainda não respondeu ({type(e).__name__}); tentando de novo…")
            time.sleep(10)
        else:
            print("✗ Servidor não respondeu.")
            sys.exit(1)

        r = c.post("/login", json={"senha": a.senha})
        if r.status_code != 200 or not r.json().get("ok"):
            print(f"✗ Login falhou ({r.status_code}): {r.text[:200]}")
            sys.exit(1)
        print("✓ Login ok")
        est = c.get("/api/estado").json()
        print(f"  versão {est.get('versao')} · modelo {est.get('modelo')} · banco {est.get('banco')} · "
              f"chave {'ok' if est.get('chave_configurada') else 'FALTANDO'}")

        mensagens = [
            "Oi Eva",
            "que horas são?",
            "busca na web a previsão do tempo em Florianópolis hoje",
            f"lembra que meu time é {a.time}",
            "qual meu time?",
        ]
        falhas = 0
        for i, texto in enumerate(mensagens, 1):
            print(f"\n[{i}/{len(mensagens)}] Gabriel: {texto}")
            t0 = time.time()
            try:
                r = c.post("/api/chat", json={"texto": texto, "timeout": a.timeout})
            except httpx.HTTPError as e:
                print(f"  ✗ erro de rede: {type(e).__name__}: {e}")
                falhas += 1
                continue
            dt = time.time() - t0
            if r.status_code != 200:
                print(f"  ✗ HTTP {r.status_code}: {r.text[:300]}")
                falhas += 1
                continue
            d = r.json()
            ferramentas = [e.get("ferramenta") for e in d.get("eventos", []) if e.get("tipo") == "acao"]
            erros = [e.get("texto") for e in d.get("eventos", []) if e.get("tipo") == "erro"]
            print(f"  Eva ({d.get('modelo')}, {dt:.1f}s{', ferramentas: ' + ', '.join(ferramentas) if ferramentas else ''}):")
            print("  " + (d.get("resposta") or "(sem resposta)").replace("\n", "\n  "))
            if erros or d.get("status") != "concluida":
                falhas += 1
                print(f"  ⚠ status={d.get('status')} erros={erros}")
            if i == 4:
                time.sleep(4)  # dá tempo da memória automática (em segundo plano) gravar

        ultima = (d.get("resposta") or "").lower() if falhas < len(mensagens) else ""
        print("\n" + "=" * 60)
        if a.time.lower() in ultima:
            print(f"✓ Memória funcionou: ela lembrou do time ({a.time}).")
        else:
            print(f"⚠ A última resposta não mencionou '{a.time}'. Confira a aba Memória.")
        print(f"Resultado: {len(mensagens) - falhas}/{len(mensagens)} mensagens sem erro.")
        sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
