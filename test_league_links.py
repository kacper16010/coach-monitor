from urllib.request import Request, urlopen
import re
from playwright.sync_api import sync_playwright


SUPERSCORE_TABLE_URL = "https://superscore.live/pl-PL/pilka-nozna/rozgrywki/ekstraklasa/0ayigwtr/klasyfikacja?season=33WqTu7gkUPTXyAK0iMzm"
NINETYMINUT_TABLE_URL = "http://www.90minut.pl/liga/1/liga14675.html"


def get_superscore_clubs(url):
    clubs = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        links = page.locator("a").evaluate_all("""
            elements => elements.map(a => ({
                text: a.innerText,
                href: a.href
            }))
        """)

        browser.close()

    for link in links:
        text = link["text"].strip()
        href = link["href"]

        if "/pilka-nozna/druzyny/" in href and text:
            if not href.endswith("/sklad"):
                href = href.rstrip("/") + "/sklad"

            clubs[text] = href

    return clubs


def get_90minut_clubs(url):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    response = urlopen(request)

    html = response.read().decode("iso-8859-2", errors="ignore")

    pattern = r'href="([^"]*skarb\.php\?id_klub=\d+[^"]*)"[^>]*>(.*?)</a>'
    matches = re.findall(pattern, html, flags=re.IGNORECASE)

    clubs = {}

    for href, name in matches:
        name = re.sub("<.*?>", "", name).strip()

        if not name:
            continue

        if href.startswith("http"):
            full_url = href
        elif href.startswith("/"):
            full_url = "http://www.90minut.pl" + href
        else:
            full_url = "http://www.90minut.pl/" + href

        clubs[name] = full_url

    return clubs


superscore_clubs = get_superscore_clubs(SUPERSCORE_TABLE_URL)
ninetyminut_clubs = get_90minut_clubs(NINETYMINUT_TABLE_URL)

print("SUPERSCORE CLUBS:")
print(len(superscore_clubs))
for club, url in superscore_clubs.items():
    print(club, "->", url)

print()
print("90MINUT CLUBS:")
print(len(ninetyminut_clubs))
for club, url in ninetyminut_clubs.items():
    print(club, "->", url)