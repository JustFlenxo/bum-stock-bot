import os, json, requests, asyncio, re, time, traceback
from bs4 import BeautifulSoup
import discord
from discord.ext import tasks
from datetime import datetime, timezone

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

FAMILY_RULES = {
    "Dum Bum": ["dum bum", "dumbum", "dum-bum"],
    "Zom Bum": ["zom bum", "zombum", "zom-bum"],
    "Viper": ["viper"],
    "Original": ["original"],
}

STATE_FILE = "state.json"
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

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
    "assortment", "set", "fan", "compound"
]

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
        fam = classify_family(title)

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
        time.sleep(0.3)
    return merged

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_state(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

intents = discord.Intents.default()
client = discord.Client(intents=intents)

def build_grouped_embed(products: dict) -> discord.Embed:
    now_utc = datetime.now(timezone.utc)
    embed = discord.Embed(
        title="🧨 Firecrackers Live Stock (Grouped)",
        description=f"Auto-updates every 10 minutes • Last update: **{now_utc.strftime('%Y-%m-%d %H:%M UTC')}**",
        color=0x1abc9c,
        timestamp=now_utc
    )

    families = {"Dum Bum": [], "Zom Bum": [], "Viper": [], "Original": [], "Other": []}
    for name, info in products.items():
        families.get(info.get("family", "Other"), families["Other"]).append((name, info))

    def format_line(name, info):
        st = info["stock"]
        link = info["link"]
        brand = info.get("brand", "Unknown")
        price = info.get("price", "—")
        emoji = "❌" if is_sold_out(st) else "✅"
        # ONE product per line
        return f"{emoji} **[{name}]({link})** — _{brand}_ — **{price}** → `{st}`"

    def add_family_fields(fam_name, items):
        if not items:
            return
        items.sort(key=lambda it: (is_sold_out(it[1]["stock"]), it[0].lower()))
        lines = [format_line(n, i) for n, i in items]

        chunk, part = "", 1
        for ln in lines:
            if len(chunk) + len(ln) + 1 > 950:
                embed.add_field(name=f"{fam_name} ({part})", value=chunk, inline=False)
                chunk, part = "", part + 1
            chunk += ln + "\n"
        if chunk:
            embed.add_field(name=f"{fam_name} ({part})", value=chunk, inline=False)

    for fam in ["Dum Bum", "Zom Bum", "Viper", "Original", "Other"]:
        add_family_fields(fam, families[fam])

    embed.set_footer(text="Only firecrackers/petards • Links + prices")
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
            title="🧨 Firecrackers Live Stock (Grouped)",
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
        await status_msg.edit(embed=build_grouped_embed(current))
        print("Status message updated.")
    except Exception:
        print("check_stock crashed but will continue:")
        traceback.print_exc()

@client.event
async def on_ready():
    print("Logged in as", client.user)
    if not check_stock.is_running():
        check_stock.start()

client.run(TOKEN)
