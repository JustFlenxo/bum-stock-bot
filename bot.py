import os, json, requests, asyncio
from bs4 import BeautifulSoup
import discord
from discord.ext import tasks
from datetime import datetime

# ================= CONFIG =================
BASE_URL = "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=bum"

# Must include one of these to be considered a Dum Bum / Zom Bum item
KEYWORDS = ["dum bum", "dum-bum", "dumbum", "zom bum", "zom-bum", "zombum"]

# Words that usually mean it's NOT a firecracker (so we exclude)
BAD_WORDS = [
    "rocket", "rakete", "raketa", "rakešu", "raktete",
    "cake", "battery", "baterija", "bateria", "batteries", "multishot", "multi shot",
    "roman candle", "candle", "fountain", "mine", "shot", "launcher", "tube",
    "volcano", "spark", "flare", "signal", "whistle cake", "compound"
]

# Words that usually mean it IS a firecracker/petard (so we include)
GOOD_WORDS = [
    "fp3", "p1", "petard", "petarde", "banger", "firecracker", "cracker",
    "m80", "m100", "m150", "m200", "m300", "m500", "m-80", "m-100"
]

STATE_FILE = "state.json"
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN not set")
if CHANNEL_ID == 0:
    raise RuntimeError("DISCORD_CHANNEL_ID not set")

HEADERS = {"User-Agent": "Mozilla/5.0"}

# ================= SCRAPER =================
def fetch_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text

def is_sold_out(stock_text: str) -> bool:
    s = (stock_text or "").lower()
    return ("sold out" in s) or ("out of stock" in s) or ("nav pieejams" in s)

def is_firecracker_title(title: str) -> bool:
    low = title.lower()

    # must be dum bum or zom bum
    if not any(k in low for k in KEYWORDS):
        return False

    # exclude obvious non-firecrackers
    if any(b in low for b in BAD_WORDS):
        return False

    # include only if it looks like a firecracker
    if any(g in low for g in GOOD_WORDS):
        return True

    # fallback: some firecrackers don't include GOOD_WORDS,
    # so allow titles that contain typical small-pack cues
    SMALL_CUES = ["pcs", "pack", "petar", "petarde"]
    return any(cue in low for cue in SMALL_CUES)

def parse_products():
    # Scrape ONLY Dum Bum / Zom Bum firecrackers from the search page.
    html = fetch_html(BASE_URL)
    soup = BeautifulSoup(html, "html.parser")
    products = {}

    for block in soup.select("div.product-block"):
        title_el = block.select_one("h2.title a")
        if not title_el:
            continue

        title = title_el.get_text(" ", strip=True)

        if not is_firecracker_title(title):
            continue

        stock_el = block.select_one("div.p-avail a.prod-available")
        stock = stock_el.get_text(" ", strip=True) if stock_el else "UNKNOWN"

        link = title_el.get("href") or BASE_URL
        if link and not link.startswith("http"):
            link = "https://www.ekopyro.eu" + link

        products[title] = {"stock": stock, "link": link}

    return products

# ================= STATE =================
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"__init": False}

def save_state(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

# ================= DISCORD =================
intents = discord.Intents.default()
client = discord.Client(intents=intents)

def build_initial_embed(products: dict):
    in_stock, sold_out, unknown = [], [], []

    for name, info in products.items():
        st = info["stock"]
        if st == "UNKNOWN":
            unknown.append((name, st))
        elif is_sold_out(st):
            sold_out.append((name, st))
        else:
            in_stock.append((name, st))

    embed = discord.Embed(
        title="🔥 Dum Bum / Zom Bum FIRECRACKERS — Initial Stock",
        description=f"Found **{len(products)}** matching firecracker products.",
        color=0x3498db,
        timestamp=datetime.utcnow()
    )

    def fmt(lines):
        return "\n".join([f"• **{n}** → `{s}`" for n, s in lines]) or "—"

    embed.add_field(name=f"✅ In stock ({len(in_stock)})", value=fmt(in_stock), inline=False)
    embed.add_field(name=f"❌ Sold out ({len(sold_out)})", value=fmt(sold_out), inline=False)
    if unknown:
        embed.add_field(name=f"❓ Unknown ({len(unknown)})", value=fmt(unknown), inline=False)

    embed.set_footer(text="Ekopyro Dum Bum / Zom Bum Firecracker Monitor")
    return embed

def build_change_embed(title, before, now, link):
    before_so = is_sold_out(before)
    now_so = is_sold_out(now)

    if before_so and not now_so:
        status = "✅ **RESTOCKED (firecracker)**"
        color = 0x2ecc71
    elif not before_so and now_so:
        status = "❌ **SOLD OUT (firecracker)**"
        color = 0xe74c3c
    else:
        status = "ℹ️ **Stock status changed**"
        color = 0xf1c40f

    embed = discord.Embed(
        title=title,
        description=status,
        color=color,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Before", value=before or "UNKNOWN", inline=True)
    embed.add_field(name="Now", value=now or "UNKNOWN", inline=True)
    embed.add_field(name="Link", value=link, inline=False)
    embed.set_footer(text="Ekopyro Dum Bum / Zom Bum Firecracker Monitor")
    return embed

@tasks.loop(minutes=10)
async def check_stock():
    ch = client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)
    state = load_state()

    current = await asyncio.to_thread(parse_products)
    if not current:
        print("No firecracker products found this run.")
        return

    if not state.get("__init"):
        state["__init"] = True
        for name, info in current.items():
            state[name] = info["stock"]
        save_state(state)

        await ch.send(embed=build_initial_embed(current))
        print("Initial firecracker list sent.")
        return

    changes = []
    for name, info in current.items():
        prev = state.get(name, "UNKNOWN")
        now = info["stock"]
        if prev != now:
            changes.append((name, prev, now, info["link"]))
        state[name] = now

    for old in list(state.keys()):
        if old not in current and old != "__init":
            del state[old]

    save_state(state)

    for name, prev, now, link in changes:
        await ch.send(embed=build_change_embed(name, prev, now, link))

@client.event
async def on_ready():
    print("Logged in as", client.user)
    if not check_stock.is_running():
        check_stock.start()

client.run(TOKEN)
