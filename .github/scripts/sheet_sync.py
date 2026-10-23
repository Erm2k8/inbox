#!/usr/bin/env python3
"""Envia para a planilha (Apps Script) os registros pendentes de planilha/fila.jsonl.

- fila.jsonl: o Claude só acrescenta linhas (uma por registro).
- enviado.txt: este script só acrescenta os ids já entregues.
Os dois arquivos são append-only e distintos, então não há conflito de merge.
O Apps Script também ignora ids repetidos, então reenviar é seguro.

Env: SHEETS_WEBHOOK_URL, SHEETS_SECRET
"""
import json
import os
import re
import sys
import urllib.request

HOOK = os.environ.get("SHEETS_WEBHOOK_URL", "")
SECRET = os.environ.get("SHEETS_SECRET", "")
FILA = "planilha/fila.jsonl"
ENVIADO = "planilha/enviado.txt"

GASTO_CATS = {"Mercado", "Lazer", "Transporte", "Assinatura", "Imprevisto"}
DESEJO_CATS = {"Moto", "Computador", "Roupa", "Casa", "Outros"}
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate(rec):
    if not rec.get("id"):
        return "sem id"
    if rec.get("tipo") == "gasto":
        if not ISO.match(str(rec.get("data", ""))):
            return "data inválida (use AAAA-MM-DD)"
        if not isinstance(rec.get("valor"), (int, float)):
            return "valor inválido"
        if not rec.get("descricao"):
            return "sem descrição"
        if rec.get("categoria") not in GASTO_CATS:
            return "categoria de gasto inválida"
    elif rec.get("tipo") == "desejo":
        if not rec.get("item"):
            return "sem item"
        if rec.get("categoria") not in DESEJO_CATS:
            return "categoria de desejo inválida"
        if rec.get("valor") is not None and not isinstance(rec["valor"], (int, float)):
            return "valor inválido"
    else:
        return "tipo desconhecido"
    return None


def post(rec):
    payload = dict(rec, secret=SECRET)
    req = urllib.request.Request(HOOK, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.load(r)
    if not out.get("ok"):
        raise RuntimeError(out.get("error", "erro"))


def main():
    if not os.path.exists(FILA):
        print("sem fila")
        return 0
    if not HOOK or not SECRET:
        print("webhook não configurado", file=sys.stderr)
        return 1
    sent = set()
    if os.path.exists(ENVIADO):
        sent = {l.strip() for l in open(ENVIADO, encoding="utf-8") if l.strip()}
    done, failed = [], 0
    for n, line in enumerate(open(FILA, encoding="utf-8"), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            print(f"linha {n}: JSON inválido, ignorada", file=sys.stderr)
            failed += 1
            continue
        if str(rec.get("id")) in sent:
            continue
        err = validate(rec)
        if err:
            print(f"linha {n}: {err}, ignorada", file=sys.stderr)
            failed += 1
            continue
        try:
            post(rec)
            done.append(str(rec["id"]))
        except Exception as e:
            print(f"linha {n}: falha no envio ({type(e).__name__})", file=sys.stderr)
            failed += 1
    if done:
        with open(ENVIADO, "a", encoding="utf-8") as f:
            f.write("\n".join(done) + "\n")
    print(f"{len(done)} enviado(s), {failed} com problema")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"sent={len(done)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
