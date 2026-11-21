"""
Telegram deals bot – "عروض إيطاليا بالعربي"
Runs every ~10 minutes on GitHub Actions (no server needed).

How it works:
  1. The admin sends an Amazon.it link to the bot in private (from the Amazon app "Share" button).
     Optional line 2: short Arabic title. (No prices: Amazon forbids manually typed prices.)
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
    "(متكتبش أسعار: شروط أمازون بتمنع نشر سعر مكتوب باليد)\n\n"
    "الأوامر:\n"
    "/queue – العروض اللي مستنية النشر\n"
    "/now – انشر العرض الجاي حالاً\n"
    "/del 2 – امسح العرض رقم 2\n"
    "/clear – امسح الطابور كله\n\n"
    "⏱ البوت بيشتغل كل دقيقة تقريباً."
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
    st.setdefault("camp_i", 0)
    st.setdefault("last_slot", None)
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

    title, hook, new_p, old_p = "", "", None, None
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
        elif not line.lower().startswith("http"):
            # line 2 = Arabic title, line 3 = selling hook (why people want it)
            if not title:
                title = line[:120]
            elif not hook:
                hook = line[:200]
    return {"asin": asin, "title": title, "hook": hook, "new": new_p, "old": old_p}, None


# ---------------- post formatting ----------------
def fmt_price(x):
    s = f"{x:.2f}".replace(".", ",")
    return s[:-3] if s.endswith(",00") else s


BF_FROM, BF_TO = (11, 20), (12, 1)   # Black Friday week banner (month, day)


def is_bf(now):
    md = (now.month, now.day)
    return BF_FROM <= md <= BF_TO


def bf_line(now):
    if is_bf(now):
        return "🖤 <b>بلاك فرايداي على أمازون إيطاليا</b>\n"
    if now.month == 12 and now.day <= 24:
        return "🎄 <b>عروض الكريسماس — جهّز هداياك بدري</b>\n"
    return ""


def build_post(d, now):
    # Amazon's policies forbid showing prices that were typed by hand (only API/Amazon-served
    # prices are allowed), so posts never show a price: the link preview and Amazon page do.
    link = affiliate_link(d["asin"])
    title = html.escape(d["title"]) if d["title"] else "عرض جديد على أمازون"
    lines = [
        bf_line(now) + f"🔥 <b>{title}</b>",
        "",
        *( [f"✅ {html.escape(d['hook'])}"] if d.get("hook") else [] ),
        "💸 عليه خصم دلوقتي على أمازون إيطاليا",
        "🚚 توصيل سريع لحد البيت • إرجاع سهل",
        "",
        f"👇 <a href=\"{link}\">شوف الخصم واطلب من هنا</a>",
        "",
        "⏳ الخصم لفترة محدودة",
        "#عروض #adv",
    ]
    return "\n".join(lines), link


# ---------------- themed deal posts (no typed prices: Amazon filters the discount itself) ----------------
CAMPAIGNS_FILE = os.environ.get("CAMPAIGNS_FILE", "campaigns.json")
# 10 slots a day (Rome time): enough to stay visible without spamming the channel
SLOTS = [(9, 0), (10, 30), (12, 0), (13, 30), (15, 0), (16, 30), (18, 0), (19, 30), (21, 0), (22, 15)]
HUB_SLOTS = {(12, 0), (21, 0)}         # these slots post Amazon's daily-deals hub


def load_campaigns():
    try:
        with open(CAMPAIGNS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def tagged(url):
    if not TAG:
        return url
    return url + ("&" if "?" in url else "?") + f"tag={TAG}"


def search_link(c):
    q = requests.utils.quote(c["q"])
    # pct-off=N- : Amazon's own filter "discount N% or more", sorted by Amazon (featured)
    return tagged(f"https://www.amazon.it/s?k={q}&pct-off={int(c.get('p', 50))}-")


def build_campaign(c, now):
    link = search_link(c)
    lines = [
        bf_line(now) + f"{c['e']} <b>{html.escape(c['t'])} — خصم {int(c.get('p', 50))}% وأكتر</b>",
        "",
        f"✅ {html.escape(c['h'])}",
        "🔎 اخترنالك من أمازون إيطاليا المنتجات اللي عليها أكبر خصومات بس",
        "",
        f"👈 <a href=\"{link}\">افتح العروض دلوقتي</a>",
        "",
        "⏳ الخصومات بتتغير كل ساعة — الشاطر يلحق",
        "#عروض #adv",
    ]
    return "\n".join(lines), link


def build_hub(now):
    link = tagged("https://www.amazon.it/deals")
    lines = [
        bf_line(now) + "⚡ <b>عروض اليوم على أمازون إيطاليا</b>",
        "",
        "🔥 خصومات لحد 70% وأكتر على ماركات كبيرة — بتخلص بسرعة",
        "📱 موبايلات • 🎧 سماعات • 🛠 أدوات • 🏠 بيت ومطبخ • 👟 لبس",
        "",
        f"👈 <a href=\"{link}\">شوف كل عروض النهارده</a>",
        "",
        "#عروض_اليوم #adv",
    ]
    return "\n".join(lines), link


def current_slot(now):
    for h, m in SLOTS:
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if 0 <= (now - start).total_seconds() < 25 * 60:
            return (h, m)
    return None


def send_post(text, link, large=True, image=None):
    """Themed posts go out as a photo (our own branded card) with the text as caption."""
    if image and os.path.exists(image):
        try:
            with open(image, "rb") as f:
                r = requests.post(f"{API}/sendPhoto", data={"chat_id": CHANNEL, "caption": text, "parse_mode": "HTML"},
                                  files={"photo": f}, timeout=60)
            data = r.json()
            if data.get("ok"):
                return data
            print(f"[telegram] sendPhoto failed: {data}", file=sys.stderr)
        except requests.RequestException as e:
            print(f"[telegram] sendPhoto error: {e}", file=sys.stderr)
    return tg("sendMessage", chat_id=CHANNEL, text=text, parse_mode="HTML",
              link_preview_options={"url": link, "prefer_large_media": large, "show_above_text": True})


IMG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "img")


def camp_image(i):
    return os.path.join(IMG_DIR, f"c{i:02d}.jpg")


def product_image(asin):
    """Amazon's official Associates image link (the SiteStripe "Image" link): the real product
    photo, served by Amazon itself for the ASIN, with our tag."""
    return ("https://ws-eu.amazon-adsystem.com/widgets/q?_encoding=UTF8&MarketPlace=IT"
            f"&ASIN={asin}&ServiceVersion=20070822&ID=AsinImage&WS=1&Format=_SL500_&tag={TAG}")


def send_product(d, text, link):
    """Product post = big real product photo + caption + a buy button. Falls back to a text post."""
    kb = {"inline_keyboard": [[{"text": "🛒 شوف السعر واطلب من أمازون", "url": link}]]}
    img = product_image(d["asin"])
    if real_photo(d["asin"]):   # a real photo, not Amazon's 1x1 placeholder
        res = tg("sendPhoto", chat_id=CHANNEL, photo=img, caption=text, parse_mode="HTML", reply_markup=kb)
        if res.get("ok"):
            return res
        print(f"[telegram] product photo failed: {res.get('description')}", file=sys.stderr)
    else:
        print(f"[amazon] no real photo for {d['asin']}, sending text post", file=sys.stderr)
    return tg("sendMessage", chat_id=CHANNEL, text=text, parse_mode="HTML", reply_markup=kb,
              link_preview_options={"url": link, "prefer_large_media": True, "show_above_text": True})


def real_photo(asin):
    try:
        r = requests.get(product_image(asin), timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        return r.ok and r.headers.get("Content-Type", "").startswith("image/jpeg") and len(r.content) > 3000
    except requests.RequestException:
        return False


NUM = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def send_album(a, now):
    """Themed collection: up to 10 real product photos in one post, numbered links in the caption."""
    items = [it for it in a["items"] if real_photo(it["asin"])][:10]
    if len(items) < 2:
        res = {"ok": False, "description": "مفيش صور كفاية للمجموعة"}
        for it in a["items"]:
            text, link = build_post(it, now)
            res = send_product(it, text, link)
        return res
    lines = [bf_line(now) + f"<b>{html.escape(a['title'])}</b>", ""]
    for i, it in enumerate(items):
        lines.append(f"{NUM[i]} <a href=\"{affiliate_link(it['asin'])}\">{html.escape(it['title'] or 'منتج')}</a>")
    lines += ["", "👆 دوس على اسم المنتج يفتحلك على أمازون", "⏳ الخصومات لفترة محدودة", "#عروض #adv"]
    caption = "\n".join(lines)[:1024]
    media = [{"type": "photo", "media": product_image(it["asin"])} for it in items]
    media[0].update({"caption": caption, "parse_mode": "HTML"})
    return tg("sendMediaGroup", chat_id=CHANNEL, media=media)


def publish_next(st, now):
    if not st["queue"]:
        return False
    d = st["queue"].pop(0)
    if d.get("type") == "album":
        res = send_album(d, now)
        if res.get("ok"):
            st["last_post"] = now.isoformat()
            st["posted"] = (st["posted"] + [it["asin"] for it in d["items"]])[-300:]
            st["total_posted"] += 1
            return True
        st["queue"].insert(0, d)
        if st.get("admin_id"):
            reply(st["admin_id"], f"⚠️ فشل نشر المجموعة: {res.get('description')}")
        return False
    text, link = build_post(d, now)
    res = send_product(d, text, link)
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
        if d.get("type") == "album":
            out.append(f"{i}. 🗂 مجموعة: {d['title']} ({len(d['items'])} منتج)")
            continue
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
    elif cmd == "/album":
        lines = text.splitlines()
        title = lines[0][len("/album"):].strip() or "🔥 مجموعة عروض"
        items = []
        for ln in lines[1:]:
            link_part, _, name = ln.partition("|")
            dl, err = parse_deal(link_part.strip())
            if dl:
                dl["title"] = name.strip()[:80]
                items.append(dl)
        if len(items) < 2:
            reply(chat_id, "اكتب المجموعة كده:\n/album 🎄 هدايا تحت 20€\nلينك | اسم المنتج\nلينك | اسم المنتج")
        else:
            st["queue"].append({"type": "album", "title": title, "items": items[:10], "asin": "album"})
            reply(chat_id, f"✅ المجموعة اتضافت للطابور ({len(items[:10])} منتج).")
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
            reply(chat_id, f"✅ اتضاف للطابور (رقم {len(st['queue'])}).")


def main():
    if not (TOKEN and CHANNEL):
        sys.exit("BOT_TOKEN and CHANNEL_ID are required")
    st = load_state()
    now = datetime.now(TZ)

    # one-shot "post now" (separate workflow): posts one themed offer, doesn't touch state.json
    pn = os.environ.get("POST_NOW", "").strip()
    if pn:
        if pn == "hub":
            text, link = build_hub(now)
            img = os.path.join(IMG_DIR, "hub.jpg")
        else:
            camps = load_campaigns()
            i = next((k for k, x in enumerate(camps) if x["q"] == pn), None)
            if i is None:
                i = int(pn) % len(camps) if pn.isdigit() else 0
            text, link = build_campaign(camps[i], now)
            img = camp_image(i)
        res = send_post(text, link, image=img)
        print("POST_NOW", "ok" if res.get("ok") else res)
        sys.exit(0 if res.get("ok") else 1)

    me = tg("getMe").get("result", {})
    upd = tg("getUpdates", offset=st["offset"], timeout=0, allowed_updates=["message"])
    print(f"bot=@{me.get('username')} updates={len(upd.get('result', []))} queue={len(st['queue'])}")
    for u in upd.get("result", []):
        st["offset"] = u["update_id"] + 1
        if "message" in u:
            try:
                handle(st, u["message"], now)
            except Exception as e:   # one bad message must not stop the bot
                print(f"[handle] {e}", file=sys.stderr)

    slot = current_slot(now)
    key = now.strftime("%Y-%m-%d ") + (f"{slot[0]:02d}:{slot[1]:02d}" if slot else "")
    if slot and st.get("last_slot") != key:
        st["last_slot"] = key
        if st["queue"]:
            publish_next(st, now)                      # your own picks come first
        else:
            # only real products are posted; with an empty queue we remind the admin (once a day)
            today = now.strftime("%Y-%m-%d")
            if st.get("admin_id") and st.get("empty_note") != today:
                st["empty_note"] = today
                reply(st["admin_id"], "📭 طابور العروض فاضي، ومش هنشر صور رمزية.\n"
                                      "ابعتلي لينكات منتجات من أمازون (زرار مشاركة) وأنا أنزلها بصورتها الحقيقية.")
    elif due(st, now) and len(st["queue"]) > 3:
        publish_next(st, now)                          # long queue: also post between slots

    # daily heartbeat commit keeps GitHub from pausing the schedule
    st["heartbeat"] = now.strftime("%Y-%m-%d")
    save_state(st)


if __name__ == "__main__":
    main()
