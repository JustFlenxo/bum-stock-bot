import os, json, requests, asyncio
from bs4 import BeautifulSoup
import discord
from discord.ext import tasks
from datetime import datetime, timezone

# ================= CONFIG =================
BASE_URL = "https://www.ekopyro.eu/page-search-eu/all/?s_keyword=bum"

# Must include one of these to be considered Dum Bum / Zom Bum
KEYWORDS = ["dum bum", "dum-bum", "dumbum", "zom bum", "zom-bum", "zombum"]

# Exclude non-firecrackers
BAD_WORDS = [
    "rocket", "rakete", "raketa", "rakešu", "raktete",
    "cake", "battery", "baterija", "bateria", "batteries", "multishot", "multi shot",
    "roman candle", "candle", "fountain", "mine", "shot", "launcher", "tube",
    "volcano", "spark", "flare", "signal", "whistle cake", "compound"
]

# Include indicators that it's a firecracker/petard
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

    # fallback: small-pack cues some firecrackers use
    SMALL_CUES = ["pcs", "pack", "petar", "petarde"]
    return any(cue in low for cue in SMALL_CUES)

def parse_products():
    """Scrape ONLY Dum Bum / Zom Bum firecrackers from the search page."""
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
        return {}

def save_state(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

# ================= DISCORD =================
intents = discord.Intents.default()
client = discord.Client(intents=intents)

def build_status_embed(products: dict) -> discord.Embed:
    """Create one embed showing in-stock vs sold-out firecrackers."""
    in_stock = []
    sold_out = []
    unknown = []

    # sort A-Z
    for name in sorted(products.keys(), key=lambda x: x.lower()):
        info = products[name]
        st = info["stock"]
        line = f"• **{name}** → `{st}`"
        if st == "UNKNOWN":
            unknown.append(line)
        elif is_sold_out(st):
            sold_out.append("❌ " + line)
        else:
            in_stock.append("✅ " + line)

    embed = discord.Embed(
        title="🔥 Dum Bum / Zom Bum FIRECRACKERS — Live Stock",
        description="This message auto-updates every 10 minutes.",
        color=0x3498db,
        timestamp=datetime.now(timezone.utc)
    )

    # Discord embed field limit ~1024 chars, so chunk lists
    def add_chunked_fields(title, lines, emoji):
        if not lines:
            embed.add_field(name=f"{emoji} {title}", value="—", inline=False)
            return
        chunk = ""
        part = 1
        for ln in lines:
            if len(chunk) + len(ln) + 1 > 950:
                embed.add_field(name=f"{emoji} {title} ({part})", value=chunk, inline=False)
                chunk = ""
                part += 1
            chunk += ln + "\n"
        if chunk:
            embed.add_field(name=f"{emoji} {title} ({part})", value=chunk, inline=False)

    add_chunked_fields("In stock", in_stock, "✅")
    add_chunked_fields("Sold out", sold_out, "❌")
    if unknown:
        add_chunked_fields("Unknown", unknown, "❓")

    embed.set_footer(text="Ekopyro monitor • firecrackers only")
    return embed

async def get_or_create_status_message(channel, state):
    msg_id = state.get("status_message_id")
    if msg_id:
        try:
            return await channel.fetch_message(msg_id)
        except Exception:
            pass  # message deleted or can't fetch

    # create new status message
    embed = discord.Embed(
        title="🔥 Dum Bum / Zom Bum FIRECRACKERS — Live Stock",
        description="Starting up…",
        color=0x3498db,
        timestamp=datetime.now(timezone.utc)
    )
    m = await channel.send(embed=embed)
    state["status_message_id"] = m.id
    save_state(state)
    return m

@tasks.loop(minutes=10)
async def check_stock():
    channel = client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)
    state = load_state()

    current = await asyncio.to_thread(parse_products)
    if not current:
        print("No firecracker products found this run.")
        return

    status_msg = await get_or_create_status_message(channel, state)
    embed = build_status_embed(current)

    await status_msg.edit(embed=embed)
    print("Status message updated.")

@client.event
async def on_ready():
    print("Logged in as", client.user)
    if not check_stock.is_running():
        check_stock.start()

client.run(TOKEN)
