import os, json, requests, asyncio, re, time, traceback
from bs4 import BeautifulSoup
import discord
from discord.ext import tasks
from datetime import datetime, timezone

# ================= CONFIG =================
SEARCH_URLS = [
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=fp3",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=p1",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=petard",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=petarde",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=firecracker",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=cracker",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=banger",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=dum%20bum",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=zom%20bum",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=viper",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=original",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=cobra",
]

FAMILY_RULES = {
    "Dum Bum": ["dum bum", "dumbum", "dum-bum"],
    "Zom Bum": ["zom bum", "zombum", "zom-bum"],
    "Viper": ["viper"],
    "Original": ["original"],
    "Cobra": ["cobra"],
}

STATE_FILE = "state.json"
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

# What to ping on alerts:
# examples:
#   "@here"
#   "@everyone"
#   "<@&ROLE_ID>"   (role mention)
PING_TEXT = os.getenv("PING_TEXT", "@here")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN not set")
if CHANNEL_ID == 0:
    raise RuntimeError("DISCORD_CHANNEL_ID not set")

HEADERS = {"User-Agent": "Mozilla/5.0"}

BAD_WORDS = [
    "rocket", "rakete", "raketa", "rakešu", "raktete",
    "cake", "battery", "baterija", "bateria", "batteries",
    "multishot", "multi shot",
    "roman candle", "candle", "fountain", "mine",
    "launcher", "tube", "volcano", "spark", "flare", "signal",
    "assortment", "set", "fan", "compound", "shell", "mortar",
    "smoke", "strobe", "torch", "confetti"
]

GOOD_WORDS = [
    "fp3", "p1", "petard", "petarde", "petar",
    "banger", "firecracker", "cracker",
    "m80", "m100", "m150", "m200", "m300", "m500",
    "thunder", "boom", "salute", "petardo", "petardy"
]

KNOWN_BRANDS = [
    "Dum Bum", "Zom Bum", "Viper", "Original", "Cobra",
    "Funke", "Klasek", "Triplex", "Jorge", "Pyro Moravia",
    "Zeus", "Panta", "Gaoo", "Di Blasio Elio", "Weco",
    "Piromax", "Iskra", "Nico", "Black Cat", "Lesli", "Riakeo"
]

# ================= SCRAPER HELPERS =================
def fetch_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text

def is_sold_out(stock_text: str) -> bool:
    s = (stock_text or "").lower()
    return ("sold out" in s) or ("out of stock" in s) or ("nav pieejams" in s)

def looks_like_firecracker(title: str) -> bool:
    low = title.lower()
    if any(b in low for b in BAD_WORDS):
        return False
    if any(g in low for g in GOOD_WORDS):
        return True
    SMALL_CUES = ["pcs", "pack", "petarde", "petardes"]
    return any(cue in low for cue in SMALL_CUES)

def classify_family(title: str) -> str:
    low = title.lower()
    for fam, keys in FAMILY_RULES.items():
        if any(k in low for k in keys):
            return fam
    return "Other"

def guess_brand(title: str) -> str:
    for b in KNOWN_BRANDS:
        if b.lower() in title.lower():
            return b
    first = title.split()[0]
    return first if len(first) >= 3 else "Unknown"

def extract_price(block: BeautifulSoup) -> str:
    price_el = (
        block.select_one(".price") or
        block.select_one(".p-price") or
        block.select_one("div.price") or
        block.select_one("span.price")
    )
    if not price_el:
        return "—"
    txt = " ".join(price_el.get_text(" ", strip=True).split())
    return txt or "—"

def parse_products_from_html(html: str, page_url: str):
    soup = BeautifulSoup(html, "html.parser")
    products = {}
    for block in soup.select("div.product-block"):
        title_el = block.select_one("h2.title a")
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)

        if not looks_like_firecracker(title):
            continue

        fam = classify_family(title)

        stock_el = block.select_one("div.p-avail a.prod-available")
        stock = stock_el.get_text(" ", strip=True) if stock_el else "UNKNOWN"

        link = title_el.get("href") or page_url
        if link and not link.startswith("http"):
            link = "https://www.ekopyro.eu" + link

        products[title] = {
            "stock": stock,
            "link": link,
            "brand": guess_brand(title),
            "price": extract_price(block),
            "family": fam,
        }
    return products

def scrape_all_firecrackers():
    merged = {}
    for url in SEARCH_URLS:
        try:
            html = fetch_html(url)
        except Exception as e:
            print("Fetch failed:", url, e)
            continue
        merged.update(parse_products_from_html(html, url))
        time.sleep(0.25)
    return merged

# ================= STATE =================
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_state(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

# ================= DISCORD BOT =================
intents = discord.Intents.default()
client = discord.Client(intents=intents)

def build_family_embeds(fam_name: str, items):
    now_utc = datetime.now(timezone.utc)
    items.sort(key=lambda it: (is_sold_out(it[1]['stock']), it[0].lower()))

    def format_line(name, info):
        st = info["stock"]
        link = info["link"]
        brand = info.get("brand", "Unknown")
        price = info.get("price", "—")
        emoji = "❌" if is_sold_out(st) else "✅"
        return f"{emoji} **[{name}]({link})** — _{brand}_ — **{price}** → `{st}`"

    lines = [format_line(n, i) for n, i in items]

    embeds = []
    chunk = ""
    part = 1
    max_embeds = 9

    for ln in lines:
        if len(chunk) + len(ln) + 1 > 3500:
            embeds.append(discord.Embed(
                title=f"{fam_name} (part {part})",
                description=chunk,
                color=0x1abc9c,
                timestamp=now_utc
            ))
            if len(embeds) >= max_embeds:
                break
            chunk = ""
            part += 1
        chunk += ln + "\n"

    if chunk and len(embeds) < max_embeds:
        embeds.append(discord.Embed(
            title=f"{fam_name}" + (f" (part {part})" if part > 1 else ""),
            description=chunk,
            color=0x1abc9c,
            timestamp=now_utc
        ))

    total_lines = len(lines)
    shown_lines = sum(e.description.count("\n") for e in embeds)
    if shown_lines < total_lines and embeds:
        remaining = total_lines - shown_lines
        embeds[-1].description += f"\n…and **{remaining}** more items (truncated)."

    header = discord.Embed(
        title=f"🧨 {fam_name} Firecrackers — Live Stock",
        description=f"Updated: **{now_utc.strftime('%Y-%m-%d %H:%M UTC')}**",
        color=0x1abc9c,
        timestamp=now_utc
    )
    header.set_footer(text="All firecrackers • Links + prices • Updates every 10 min")
    return [header] + embeds

async def get_or_create_family_message(channel, state, key, title):
    msg_id = state.get(key)
    if msg_id:
        try:
            return await channel.fetch_message(msg_id)
        except Exception:
            pass

    m = await channel.send(embed=discord.Embed(
        title=title,
        description="Starting up…",
        color=0x1abc9c,
        timestamp=datetime.now(timezone.utc)
    ))
    state[key] = m.id
    save_state(state)
    return m

def detect_changes(prev_products: dict, current: dict):
    """
    prev_products: title -> stock text
    current: title -> {stock, link, family, price, brand}
    returns list of (kind, title, prev_stock, new_stock, link)
    """
    changes = []
    for title, info in current.items():
        new_stock = info["stock"]
        prev_stock = prev_products.get(title)

        if prev_stock is None:
            # new item discovered - treat as info, not ping
            prev_products[title] = new_stock
            continue

        if prev_stock != new_stock:
            prev_sold = is_sold_out(prev_stock)
            new_sold = is_sold_out(new_stock)

            if prev_sold and not new_sold:
                kind = "RESTOCKED"
            elif not prev_sold and new_sold:
                kind = "SOLD OUT"
            else:
                kind = "CHANGED"

            changes.append((kind, title, prev_stock, new_stock, info["link"]))
            prev_products[title] = new_stock

    return changes

async def send_change_alerts(channel, changes):
    if not changes:
        return

    restocked = [c for c in changes if c[0] == "RESTOCKED"]
    soldout   = [c for c in changes if c[0] == "SOLD OUT"]
    other     = [c for c in changes if c[0] == "CHANGED"]

    lines = []
    if restocked:
        lines.append("✅ **RESTOCKED:**")
        for _, title, before, now, link in restocked:
            lines.append(f"- **[{title}]({link})** → `{now}`")
    if soldout:
        lines.append("")
        lines.append("❌ **SOLD OUT:**")
        for _, title, before, now, link in soldout:
            lines.append(f"- **{title}** → `{now}`")
    if other:
        lines.append("")
        lines.append("ℹ️ **STATUS CHANGED:**")
        for _, title, before, now, link in other:
            lines.append(f"- **{title}**: `{before}` → `{now}`")

    msg = f"{PING_TEXT}\n" + "\n".join(lines)
    await channel.send(msg)

@tasks.loop(minutes=10)
async def check_stock():
    try:
        channel = client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)
        state = load_state()

        current = await asyncio.to_thread(scrape_all_firecrackers)
        if not current:
            print("No firecrackers found this run.")
            return

        # --- CHANGE DETECTION ---
        prev_products = state.get("product_stocks", {})
        changes = detect_changes(prev_products, current)
        state["product_stocks"] = prev_products
        save_state(state)

        # Alert if changes happened
        await send_change_alerts(channel, changes)

        # --- LIVE FAMILY MESSAGES ---
        families = {name: [] for name in list(FAMILY_RULES.keys()) + ["Other"]}
        for name, info in current.items():
            fam = info.get("family", "Other")
            if fam not in families:
                fam = "Other"
            families[fam].append((name, info))

        order = ["Dum Bum", "Zom Bum", "Viper", "Original", "Cobra", "Other"]
        for fam_name in order:
            items = families.get(fam_name, [])
            if not items:
                continue

            key = f"{fam_name.lower().replace(' ', '_')}_msg_id"
            title = f"🧨 {fam_name} Firecrackers — Live Stock"
            msg = await get_or_create_family_message(channel, state, key, title)

            embeds = build_family_embeds(fam_name, items)
            await msg.edit(embeds=embeds)

        print("All family messages updated.")
    except Exception:
        print("check_stock crashed but will continue:")
        traceback.print_exc()

@client.event
async def on_ready():
    print("Logged in as", client.user)
    if not check_stock.is_running():
        check_stock.start()

client.run(TOKEN)
