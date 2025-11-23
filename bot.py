import os, json, requests, asyncio, re, time
from bs4 import BeautifulSoup
import discord
from discord.ext import tasks
from datetime import datetime, timezone

# ================= CONFIG =================
# We search multiple keywords to catch all relevant firecrackers.
SEARCH_URLS = [
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=fp3",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=p1",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=petard",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=petarde",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=dum%20bum",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=zom%20bum",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=viper",
    "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=original",
]

# We ONLY keep these families/brands in the final list
ALLOWED_FAMILIES = [
    "dum bum", "dumbum", "dum-bum",
    "zom bum", "zombum", "zom-bum",
    "viper",
    "original",
]

STATE_FILE = "state.json"
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN not set")
if CHANNEL_ID == 0:
    raise RuntimeError("DISCORD_CHANNEL_ID not set")

HEADERS = {"User-Agent": "Mozilla/5.0"}

# Exclude non-firecrackers
BAD_WORDS = [
    "rocket", "rakete", "raketa", "rakešu", "raktete",
    "cake", "battery", "baterija", "bateria", "batteries",
    "multishot", "multi shot",
    "roman candle", "candle", "fountain", "mine",
    "launcher", "tube", "volcano", "spark", "flare", "signal",
    "assortment", "set", "fan", "compound"
]

# Include indicators that it's a firecracker/petard
GOOD_WORDS = [
    "fp3", "p1", "petard", "petarde", "petar",
    "banger", "firecracker", "cracker",
    "m80", "m100", "m150", "m200", "m300", "m500",
    "thunder", "boom", "salute"
]

KNOWN_BRANDS = [
    "Dum Bum", "Zom Bum", "Viper", "Original",
    "Funke", "Klasek", "Triplex", "Jorge",
    "Pyro Moravia", "Zeus", "Panta", "Gaoo",
    "Di Blasio Elio", "Weco", "Piromax", "Pol-Expance"
]

# ================= SCRAPER =================
def fetch_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text

def is_sold_out(stock_text: str) -> bool:
    s = (stock_text or "").lower()
    return ("sold out" in s) or ("out of stock" in s) or ("nav pieejams" in s)

def looks_like_firecracker(title: str) -> bool:
    low = title.lower()

    # must be one of your families
    if not any(fam in low for fam in ALLOWED_FAMILIES):
        return False

    # exclude obvious non-firecrackers
    if any(b in low for b in BAD_WORDS):
        return False

    # include only if it looks like a petard/firecracker
    if any(g in low for g in GOOD_WORDS):
        return True

    # fallback: small-pack cues
    SMALL_CUES = ["pcs", "pack", "petarde", "petardes"]
    return any(cue in low for cue in SMALL_CUES)

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

def extract_nec_from_product_page(url: str) -> str:
    """
    Pull NEC / pyrotechnic powder grams from product page text.
    If not found, return '—'.
    """
    try:
        html = fetch_html(url)
    except Exception:
        return "—"

    text = " ".join(BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split())

    # matches like: "Weight of pyrotechnic powder (NEC): 42 g"
    m = re.search(r"pyrotechnic powder \\(NEC\\)[:\\s]*([0-9]+(?:[\\.,][0-9]+)?)\\s*g", text, re.I)
    if m:
        return m.group(1).replace(",", ".") + " g"

    # matches like: "NEC 0.8 g" or "NEC for product: 36 g"
    m = re.search(r"NEC[:\\s]*(?:for product[:\\s]*)?([0-9]+(?:[\\.,][0-9]+)?)\\s*g", text, re.I)
    if m:
        return m.group(1).replace(",", ".") + " g"

    return "—"

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
        }

    return products

def scrape_all_firecrackers():
    """
    Search multiple keywords, merge results, then enrich each
    product with NEC from its product page.
    """
    merged = {}
    for url in SEARCH_URLS:
        try:
            html = fetch_html(url)
        except Exception as e:
            print("Fetch failed:", url, e)
            continue
        merged.update(parse_products_from_html(html, url))
        time.sleep(0.3)

    # Enrich with NEC (grams)
    for title, info in merged.items():
        nec = extract_nec_from_product_page(info["link"])
        info["nec"] = nec
        time.sleep(0.2)

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

# ================= DISCORD =================
intents = discord.Intents.default()
client = discord.Client(intents=intents)

def build_status_embed(products: dict) -> discord.Embed:
    in_stock, sold_out, unknown = [], [], []

    for name in sorted(products.keys(), key=lambda x: x.lower()):
        info = products[name]
        st = info["stock"]
        link = info["link"]
        brand = info.get("brand", "Unknown")
        price = info.get("price", "—")
        nec = info.get("nec", "—")

        # Clean, “mature” line:
        # clickable name + brand + price + NEC + stock
        line = f"• **[{name}]({link})** — _{brand}_ — **{price}** — **NEC {nec}** → `{st}`"

        if st == "UNKNOWN":
            unknown.append(line)
        elif is_sold_out(st):
            sold_out.append(line)
        else:
            in_stock.append(line)

    now_utc = datetime.now(timezone.utc)
    embed = discord.Embed(
        title="🧨 Dum Bum / Zom Bum / Viper / Original — Firecrackers Live Stock",
        description=f"Auto-updates every 10 minutes. Last update: **{now_utc.strftime('%Y-%m-%d %H:%M UTC')}**",
        color=0x1abc9c,
        timestamp=now_utc
    )

    def add_chunked_fields(title, lines, emoji):
        if not lines:
            embed.add_field(name=f"{emoji} {title}", value="—", inline=False)
            return
        chunk, part = "", 1
        for ln in lines:
            if len(chunk) + len(ln) + 1 > 950:
                embed.add_field(name=f"{emoji} {title} ({part})", value=chunk, inline=False)
                chunk, part = "", part + 1
            chunk += ln + "\\n"
        if chunk:
            embed.add_field(name=f"{emoji} {title} ({part})", value=chunk, inline=False)

    add_chunked_fields("In stock", in_stock, "✅")
    add_chunked_fields("Sold out", sold_out, "❌")
    if unknown:
        add_chunked_fields("Unknown", unknown, "❓")

    embed.set_footer(text="Firecrackers only • Links + prices + NEC")
    return embed

async def get_or_create_status_message(channel, state):
    msg_id = state.get("status_message_id")
    if msg_id:
        try:
            return await channel.fetch_message(msg_id)
        except Exception:
            pass

    m = await channel.send(
        embed=discord.Embed(
            title="🧨 Firecrackers Live Stock",
            description="Starting up…",
            color=0x1abc9c,
            timestamp=datetime.now(timezone.utc)
        )
    )
    state["status_message_id"] = m.id
    save_state(state)
    return m

@tasks.loop(minutes=10)
async def check_stock():
    try:
        channel = client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)
        state = load_state()

        current = await asyncio.to_thread(scrape_all_firecrackers)
        if not current:
            print("No matching firecrackers found this run.")
            return

        status_msg = await get_or_create_status_message(channel, state)
        embed = build_status_embed(current)
        await status_msg.edit(embed=embed)

        print("Status message updated.")
    except Exception as e:
        print("check_stock error:", e)

@client.event
async def on_ready():
    print("Logged in as", client.user)
    if not check_stock.is_running():
        check_stock.start()

client.run(TOKEN)
