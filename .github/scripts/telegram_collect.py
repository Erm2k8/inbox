#!/usr/bin/env python3
"""Coleta mensagens do bot do Telegram e acrescenta em inbox.md.

Variáveis de ambiente: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
Só mensagens do chat autorizado são aceitas. As demais são ignoradas.
As mensagens só são confirmadas no Telegram depois do commit (etapa "ack"),
então uma falha no meio do caminho não perde nada.
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
API = f"https://api.telegram.org/bot{TOKEN}"
INBOX = os.environ.get("INBOX_FILE", "inbox.md")
STATE = os.environ.get("STATE_FILE", ".telegram_offset")  # só no runner, não vai pro git
TZ = timezone(timedelta(hours=-3))  # America/Fortaleza


def call(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(f"{API}/{method}", data=data, timeout=30) as r:
        out = json.load(r)
    if not out.get("ok"):
        raise RuntimeError(out)
    return out["result"]


def collect():
    updates = call("getUpdates", timeout=0, allowed_updates=json.dumps(["message"]))
    lines, last_id = [], None
    for u in updates:
        last_id = u["update_id"]
        m = u.get("message") or {}
        if m.get("chat", {}).get("id") != CHAT_ID:
            continue
        text = (m.get("text") or m.get("caption") or "").strip()
        if not text:
            text = "[mensagem sem texto: foto/áudio/arquivo - tratar manualmente]"
        when = datetime.fromtimestamp(m["date"], TZ).strftime("%Y-%m-%d %H:%M")
        text = " ".join(text.splitlines())  # uma linha por item
        lines.append(f"- [{when}] {text}")
    if lines:
        with open(INBOX, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    if last_id is not None:
        with open(STATE, "w") as f:
            f.write(str(last_id + 1))
    print(f"{len(lines)} item(ns) coletado(s)")
    return len(lines)


def ack():
    """Confirma as mensagens no Telegram (depois do push) e avisa o usuário."""
    if not os.path.exists(STATE):
        return
    offset = int(open(STATE).read())
    call("getUpdates", offset=offset, timeout=0)
    os.remove(STATE)
    n = int(os.environ.get("COLLECTED", "0"))
    if n:
        call("sendMessage", chat_id=CHAT_ID, text=f"Anotado no inbox ({n}).")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "ack":
        ack()
    else:
        n = collect()
        gh_out = os.environ.get("GITHUB_OUTPUT")
        if gh_out:
            with open(gh_out, "a") as f:
                f.write(f"collected={n}\n")
