#!/usr/bin/env python3
"""Coleta mensagens do bot do Telegram.

- `g ...` (gasto) e `c ...` (desejo): vão direto para a planilha (Apps Script)
  e são registradas em log/AAAA-MM.md.
- Qualquer outra mensagem (t, e, i, sem prefixo): vai para inbox.md.
- Se o envio à planilha falhar ou o formato não for entendido, a mensagem
  vai para inbox.md e o bot avisa.
As mensagens só são confirmadas no Telegram depois do commit (etapa "ack").

Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SHEETS_WEBHOOK_URL, SHEETS_SECRET
"""
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
HOOK = os.environ.get("SHEETS_WEBHOOK_URL", "")
SECRET = os.environ.get("SHEETS_SECRET", "")
API = f"https://api.telegram.org/bot{TOKEN}"
INBOX = "inbox.md"
LOG_DIR = "log"
STATE = ".telegram_offset"
REPLIES = ".telegram_replies"
TZ = timezone(timedelta(hours=-3))  # America/Fortaleza

GASTO_CATS = ["Mercado", "Lazer", "Transporte", "Assinatura", "Imprevisto"]
DESEJO_CATS = ["Moto", "Computador", "Roupa", "Casa", "Outros"]

MONEY = re.compile(r"^(?:r\$)?(\d+(?:[.,]\d+)*)$", re.I)
DATE = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$")
URL = re.compile(r"^https?://", re.I)


class ParseError(Exception):
    pass


def norm(s):
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()


def parse_money(tok):
    t = MONEY.match(tok).group(1)
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif "." in t and re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")  # 1.500 -> 1500
    return float(t)


def brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def match_cat(tok, cats):
    n = norm(tok)
    for c in cats:
        nc = norm(c)
        if n == nc or (len(n) >= 4 and nc.startswith(n)):
            return c
    return None


def parse_date(tok, base):
    if norm(tok) == "hoje":
        return base.date()
    if norm(tok) == "ontem":
        return (base - timedelta(days=1)).date()
    m = DATE.match(tok)
    if not m:
        return None
    d, mo, y = int(m[1]), int(m[2]), m[3]
    y = base.year if not y else (int(y) + 2000 if len(y) == 2 else int(y))
    try:
        return datetime(y, mo, d).date()
    except ValueError:
        return None


def parse_gasto(rest, base):
    value, cat, day, desc = None, None, base.date(), []
    for tok in rest.split():
        if tok.lower() == "r$":
            continue
        dt = parse_date(tok, base)
        if dt and (norm(tok) in ("hoje", "ontem") or "/" in tok):
            day = dt
        elif value is None and MONEY.match(tok):
            value = parse_money(tok)
        elif cat is None and match_cat(tok, GASTO_CATS):
            cat = match_cat(tok, GASTO_CATS)
        else:
            desc.append(tok)
    if value is None:
        raise ParseError("sem valor")
    cat = cat or "Sem categoria"
    description = " ".join(desc) or cat
    return {"tipo": "gasto", "data": day.isoformat(), "descricao": description,
            "categoria": cat, "valor": value}


def parse_desejo(rest, base):
    toks = [t for t in rest.split() if t.lower() != "r$"]
    link = next((t for t in toks if URL.match(t)), "")
    toks = [t for t in toks if not URL.match(t)]
    idx = max((i for i, t in enumerate(toks) if MONEY.match(t)), default=None)
    value = None
    if idx is not None:
        value = parse_money(toks[idx])
        toks.pop(idx)
    cat = next((match_cat(t, DESEJO_CATS) for t in toks if match_cat(t, DESEJO_CATS)), "Outros")
    item = " ".join(toks)
    if not item:
        raise ParseError("sem item")
    return {"tipo": "desejo", "item": item, "categoria": cat, "valor": value, "link": link}


def post_sheet(payload):
    if not HOOK or not SECRET:
        raise RuntimeError("webhook não configurado")
    payload = dict(payload, secret=SECRET)
    req = urllib.request.Request(HOOK, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.load(r)
    if not out.get("ok"):
        raise RuntimeError(out)


def call(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(f"{API}/{method}", data=data, timeout=30) as r:
        out = json.load(r)
    if not out.get("ok"):
        raise RuntimeError(out)
    return out["result"]


def append(path, text, header=None):
    new = not os.path.exists(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        if new and header:
            f.write(header)
        f.write(text)


def collect():
    updates = call("getUpdates", timeout=0, allowed_updates=json.dumps(["message"]))
    inbox, logs, replies, last_id = [], {}, [], None
    for u in updates:
        last_id = u["update_id"]
        m = u.get("message") or {}
        if m.get("chat", {}).get("id") != CHAT_ID:
            continue
        text = (m.get("text") or m.get("caption") or "").strip()
        if not text:
            text = "[mensagem sem texto: foto/áudio/arquivo - tratar manualmente]"
        text = " ".join(text.splitlines())
        base = datetime.fromtimestamp(m["date"], TZ)
        when = base.strftime("%Y-%m-%d %H:%M")
        head, _, rest = text.partition(" ")
        kind = head.lower()

        if kind in ("g", "c") and rest.strip():
            try:
                rec = (parse_gasto if kind == "g" else parse_desejo)(rest.strip(), base)
                post_sheet(dict(rec, id=str(u["update_id"])))
                if kind == "g":
                    d = rec["data"]
                    summary = (f"Gasto: {brl(rec['valor'])} · {rec['categoria']} · "
                               f"{d[8:]}/{d[5:7]}/{d[:4]} · {rec['descricao']}")
                    dest = "Gastos"
                else:
                    summary = (f"Desejo: {rec['item']} · {rec['categoria']}"
                               + (f" · {brl(rec['valor'])}" if rec["valor"] is not None else "")
                               + (f" · {rec['link']}" if rec["link"] else ""))
                    dest = "Desejos"
                logs.setdefault(base.strftime("%Y-%m"), []).append(
                    f"- [{when}] {text} → planilha {dest} | {summary}")
                replies.append(summary)
                continue
            except ParseError as e:
                replies.append(f"Não entendi ({e}). Deixei no inbox: {text}")
            except Exception as e:  # rede, webhook, segredo...
                print(f"falha ao enviar à planilha: {type(e).__name__}", file=sys.stderr)
                replies.append(f"Planilha indisponível. Deixei no inbox: {text}")
        else:
            replies.append("Anotado no inbox.")
        inbox.append(f"- [{when}] {text}")

    if inbox:
        append(INBOX, "\n".join(inbox) + "\n")
    for ym, lines in logs.items():
        append(f"{LOG_DIR}/{ym}.md", "\n".join(lines) + "\n", header=f"# Log {ym}\n\n")
    if replies:
        with open(REPLIES, "w", encoding="utf-8") as f:
            json.dump(replies, f, ensure_ascii=False)
    if last_id is not None:
        with open(STATE, "w") as f:
            f.write(str(last_id + 1))
    n = len(inbox) + sum(len(v) for v in logs.values())
    print(f"{len(inbox)} no inbox, {n - len(inbox)} na planilha")
    return n


def ack():
    """Confirma as mensagens no Telegram (depois do push) e responde ao usuário."""
    if not os.path.exists(STATE):
        return
    offset = int(open(STATE).read())
    call("getUpdates", offset=offset, timeout=0)
    os.remove(STATE)
    if os.path.exists(REPLIES):
        replies = json.load(open(REPLIES, encoding="utf-8"))
        os.remove(REPLIES)
        if replies:
            call("sendMessage", chat_id=CHAT_ID, text="\n".join(replies))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "ack":
        ack()
    else:
        n = collect()
        gh_out = os.environ.get("GITHUB_OUTPUT")
        if gh_out:
            with open(gh_out, "a") as f:
                f.write(f"collected={n}\n")