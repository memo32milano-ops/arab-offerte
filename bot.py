"""
Telegram deals bot – "عروض إيطاليا بالعربي"
Runs every ~10 minutes on GitHub Actions (no server needed).

How it works:
  1. The admin sends an Amazon.it link to the bot in private (from the Amazon app "Share" button).
     Optional line 2: short Arabic title.  Optional line 3: "new_price old_price"  e.g.  19.99 34.99
  2. The bot cleans the link, adds the affiliate tag, and puts the deal in a queue.
  3. The bot publishes deals to the channel at good times (08:00-22:30 Rome), spaced out,
     so the channel stays active all day even if you add 20 deals at once.

Commands (private chat with the bot):
  /start  /help   – help
  /queue          – show the queue
  /now            – publish the next deal immediately
  /del N          – delete deal number N from the queue
  /clear          – empty the queue
"""
import html
import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL = os.environ.get("CHANNEL_ID", "")          # e.g. @ArabOfferteItalia
TAG = os.environ.get("AMAZON_TAG", "")              # e.g. ventistudios-21
ADMIN_ENV = os.environ.get("ADMIN_ID", "").strip()  # optional
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

TZ = ZoneInfo("Europe/Rome")
POST_FROM_HOUR, POST_TO_HOUR = 8, 22   # posting window (Rome time), last post before 22:59
NORMAL_GAP_MIN = 90                    # minutes between posts
FAST_GAP_MIN = 40                      # when the queue is long (> FAST_QUEUE)
FAST_QUEUE = 8

API = f"https://api.telegram.org/bot{TOKEN}"
ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})", re.I)
URL_RE = re.compile(r"https?://\S+")
PRICE_LINE_RE = re.compile(r"^\s*€?\s*(\d+(?:[.,]\d{1,2})?)\s*€?(?:\s+€?\s*(\d+(?:[.,]\d{1,2})?)\s*€?)?\s*$")

HELP = (
    "أهلاً 👋\n"
    "ابعتلي لينك منتج من أمازون إيطاليا (من زرار المشاركة في تطبيق أمازون).\n\n"
    "الشكل:\n"
    "السطر 1: اللينك\n"
    "السطر 2 (اختياري): عنوان قصير بالعربي\n"
    "السطر 3 (اختياري): السعر الجديد والقديم، مثلاً: 19.99 34.99\n\n"
    "الأوامر:\n"
    "/queue – العروض اللي مستنية النشر\n"
    "/now – انشر العرض الجاي حالاً\n"
    "/del 2 – امسح العرض رقم 2\n"
    "/clear – امسح الطابور كله\n\n"
    "⏱ البوت بيشتغل كل حوالي 10 دقايق، فالرد ممكن يتأخر شوية."
)


# ---------------- state ----------------
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        st = {}
    st.setdefault("offset", 0)
    st.setdefault("queue", [])
    st.setdefault("last_post", None)
    st.setdefault("posted", [])      # recent ASINs, to avoid duplicates
    st.setdefault("admin_id", None)
    st.setdefault("heartbeat", None)
    st.setdefault("total_posted", 0)
    return st


def save_state(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


# ---------------- telegram ----------------
def tg(method, **params):
    r = requests.post(f"{API}/{method}", json=params, timeout=30)
    data = r.json()
    if not data.get("ok"):
        print(f"[telegram] {method} failed: {data}", file=sys.stderr)
    return data


def reply(chat_id, text):
    tg("sendMessage", chat_id=chat_id, text=text, link_preview_options={"is_disabled": True})


# ---------------- amazon links ----------------
def resolve(url):
    """Follow short links (amzn.eu/d/..., amzn.to/...) without loading the Amazon page."""
    for _ in range(4):
        if ASIN_RE.search(url):
            return url
        try:
            r = requests.get(url, allow_redirects=False, timeout=15,
                             headers={"User-Agent": "Mozilla/5.0 (Linux; Android 14) Mobile"})
        except requests.RequestException as e:
            print(f"[resolve] {url}: {e}", file=sys.stderr)
            return url
        loc = r.headers.get("Location")
        if not loc:
            return url
        url = requests.compat.urljoin(url, loc)
    return url


def affiliate_link(asin):
    return f"https://www.amazon.it/dp/{asin}?tag={TAG}" if TAG else f"https://www.amazon.it/dp/{asin}"


def to_float(s):
    return float(s.replace(",", ".")) if s else None


def parse_deal(text):
    """Return (deal dict | None, error message | None)."""
    m = URL_RE.search(text or "")
    if not m:
        return None, None
    url = m.group(0).rstrip(").,]")
    full = resolve(url)
    am = ASIN_RE.search(full)
    if not am or "amazon." not in full and "amzn." not in url:
        return None, "❌ مش قادر أقرا اللينك ده. ابعت لينك منتج من أمازون إيطاليا."
    asin = am.group(1).upper()

    title, new_p, old_p = "", None, None
    # lines typed after the link come first (your Arabic title), Amazon's share text last
    before = re.sub(r"(?i)(guarda|dai un'occhiata a|scopri)[^:]*:?", "", text[:m.start()])
    rest = text[m.end():].splitlines() + before.splitlines()
    for line in rest:
        line = line.strip(" \t-–:\"'")
        if not line:
            continue
        pm = PRICE_LINE_RE.match(line)
        if pm and new_p is None:
            a, b = to_float(pm.group(1)), to_float(pm.group(2))
            if b is not None and b < a:
                a, b = b, a
            new_p, old_p = a, b
        elif not title and not line.lower().startswith("http"):
            # ignore Amazon's auto text in shares like "Guarda questo prodotto su Amazon"
            title = line[:120]
    return {"asin": asin, "title": title, "new": new_p, "old": old_p}, None


# ---------------- post formatting ----------------
def fmt_price(x):
    s = f"{x:.2f}".replace(".", ",")
    return s[:-3] if s.endswith(",00") else s


def build_post(d, now):
    link = affiliate_link(d["asin"])
    lines = []
    title = html.escape(d["title"]) if d["title"] else "عرض جديد من أمازون"
    pct = None
    if d.get("new") and d.get("old") and d["old"] > d["new"]:
        pct = round((1 - d["new"] / d["old"]) * 100)
    head = "🔥" if pct and pct >= 40 else "⚡️"
    lines.append(f"{head} <b>{title}</b>")
    lines.append("")
    if d.get("new"):
        if pct:
            lines.append(f"💰 السعر: <b>{fmt_price(d['new'])}€</b> بدل <s>{fmt_price(d['old'])}€</s>")
            lines.append(f"📉 خصم <b>{pct}%</b>")
        else:
            lines.append(f"💰 السعر: <b>{fmt_price(d['new'])}€</b>")
    lines.append(f"🛒 <a href=\"{link}\">اطلب من هنا</a>")
    lines.append("")
    if d.get("new"):
        lines.append(f"⏳ السعر وقت النشر ({now:%d/%m %H:%M}) وممكن يتغير في أي لحظة")
    lines.append("#عروض #adv")
    return "\n".join(lines), link


def publish_next(st, now):
    if not st["queue"]:
        return False
    d = st["queue"].pop(0)
    text, link = build_post(d, now)
    res = tg("sendMessage", chat_id=CHANNEL, text=text, parse_mode="HTML",
             link_preview_options={"url": link, "prefer_large_media": True, "show_above_text": True})
    if res.get("ok"):
        st["last_post"] = now.isoformat()
        st["posted"] = (st["posted"] + [d["asin"]])[-300:]
        st["total_posted"] += 1
        return True
    st["queue"].insert(0, d)   # keep it for the next run
    if st.get("admin_id"):
        reply(st["admin_id"], f"⚠️ فشل النشر في القناة: {res.get('description')}\n"
                              "اتأكد إن البوت أدمن في القناة وإن CHANNEL_ID صح.")
    return False


def due(st, now):
    if not st["queue"] or not (POST_FROM_HOUR <= now.hour <= POST_TO_HOUR):
        return False
    if not st["last_post"]:
        return True
    gap = FAST_GAP_MIN if len(st["queue"]) > FAST_QUEUE else NORMAL_GAP_MIN
    last = datetime.fromisoformat(st["last_post"])
    return (now - last).total_seconds() >= gap * 60


# ---------------- incoming messages ----------------
def queue_text(st):
    if not st["queue"]:
        return "الطابور فاضي. ابعت لينكات 👍"
    out = [f"📋 في الطابور {len(st['queue'])} عرض:"]
    for i, d in enumerate(st["queue"], 1):
        p = f" – {fmt_price(d['new'])}€" if d.get("new") else ""
        out.append(f"{i}. {d['title'] or d['asin']}{p}")
    return "\n".join(out)


def handle(st, msg, now):
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id")
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if msg["chat"].get("type") != "private":
        return

    admin = int(ADMIN_ENV) if ADMIN_ENV else st.get("admin_id")
    if admin is None:            # first person to talk to the bot becomes the admin
        st["admin_id"] = admin = user_id
        reply(chat_id, "✅ اتسجلت كأدمن للبوت.\n\n" + HELP)
        return
    if user_id != admin:
        reply(chat_id, "البوت ده خاص بإدارة القناة 🙏")
        return
    st["admin_id"] = admin

    cmd = text.split()[0].lower() if text.startswith("/") else ""
    if cmd in ("/start", "/help"):
        reply(chat_id, HELP)
    elif cmd == "/queue":
        reply(chat_id, queue_text(st))
    elif cmd == "/now":
        ok = publish_next(st, now)
        reply(chat_id, "✅ اتنشر في القناة" if ok else "مفيش حاجة اتنشرت (الطابور فاضي أو حصل خطأ).")
    elif cmd == "/clear":
        st["queue"] = []
        reply(chat_id, "🗑 الطابور اتمسح.")
    elif cmd == "/del":
        try:
            n = int(text.split()[1])
            d = st["queue"].pop(n - 1)
            reply(chat_id, f"🗑 اتمسح: {d['title'] or d['asin']}")
        except (IndexError, ValueError):
            reply(chat_id, "اكتب رقم العرض، مثلاً: /del 2")
    else:
        deal, err = parse_deal(text)
        if err:
            reply(chat_id, err)
        elif not deal:
            reply(chat_id, HELP)
        elif deal["asin"] in st["posted"][-60:] or any(q["asin"] == deal["asin"] for q in st["queue"]):
            reply(chat_id, "⚠️ المنتج ده اتنشر أو موجود في الطابور قريب. مش هكرره.")
        else:
            st["queue"].append(deal)
            extra = "" if deal.get("new") else "\n💡 نصيحة: ضيف السعر الجديد والقديم في سطر لوحده عشان يظهر الخصم."
            reply(chat_id, f"✅ اتضاف للطابور (رقم {len(st['queue'])}).{extra}")


def main():
    if not (TOKEN and CHANNEL):
        sys.exit("BOT_TOKEN and CHANNEL_ID are required")
    st = load_state()
    now = datetime.now(TZ)

    upd = tg("getUpdates", offset=st["offset"], timeout=0, allowed_updates=["message"])
    for u in upd.get("result", []):
        st["offset"] = u["update_id"] + 1
        if "message" in u:
            try:
                handle(st, u["message"], now)
            except Exception as e:   # one bad message must not stop the bot
                print(f"[handle] {e}", file=sys.stderr)

    if due(st, now):
        publish_next(st, now)

    # daily heartbeat commit keeps GitHub from pausing the schedule
    st["heartbeat"] = now.strftime("%Y-%m-%d")
    save_state(st)


if __name__ == "__main__":
    main()
