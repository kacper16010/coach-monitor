from urllib.request import Request, urlopen
import csv
import re
import time
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from playwright.sync_api import sync_playwright
import argparse
import os

start_time = time.time()

COACH_ALIASES = {
    "yuriy shatalov": "jurij szatalow",
}


MAX_WORKERS = 6


def normalize_name(name):
    if not name:
        return None

    name = name.strip()
    name = re.sub(r"\s+[A-Z]{3}(\/[A-Z]{3})*$", "", name).strip()

    polish_chars = {
        "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
        "ó": "o", "ś": "s", "ź": "z", "ż": "z",
        "Ą": "a", "Ć": "c", "Ę": "e", "Ł": "l", "Ń": "n",
        "Ó": "o", "Ś": "s", "Ź": "z", "Ż": "z",
    }

    for polish, latin in polish_chars.items():
        name = name.replace(polish, latin)

    name = unicodedata.normalize("NFKD", name)
    name = "".join(char for char in name if not unicodedata.combining(char))
    name = " ".join(name.lower().split())

    name = COACH_ALIASES.get(name, name)

    return name


def get_superscore_coach(browser, url):
    page = browser.new_page()

    blocked_words = [
        "TRENER",
        "NAPASTNICY",
        "POMOCNICY",
        "OBROŃCY",
        "OBRONCY",
        "BRAMKARZE",
        "INNE",
        "SKŁAD",
        "SKLAD",
        "MECZE",
        "TABELA",
        "STATYSTYKI",
        "NAJLEPSI GRACZE",
        "INFORMACJE O DRUŻYNIE",
        "INFORMACJE O DRUZYNIE",
    ]

    name_pattern = re.compile(
        r"^[A-ZĄĆĘŁŃÓŚŹŻÁÉÍÓÚÝČŠŽĽĹŔÄÖÜ][a-ząćęłńóśźżáéíóúýčšžľĺŕäöü]+"
        r"(?:\s+[A-ZĄĆĘŁŃÓŚŹŻÁÉÍÓÚÝČŠŽĽĹŔÄÖÜ][a-ząćęłńóśźżáéíóúýčšžľĺŕäöü]+)+$"
    )

    def extract_from_lines(lines):
        for i, line in enumerate(lines):
            if "TRENER" not in line.upper():
                continue

            for candidate in lines[i + 1:i + 10]:
                candidate = candidate.strip()
                upper_candidate = candidate.upper()

                if not candidate:
                    continue

                if any(word in upper_candidate for word in blocked_words):
                    continue

                if re.fullmatch(r"\d+", candidate):
                    continue

                if "LAT" in upper_candidate:
                    continue

                if candidate.startswith("("):
                    continue

                if name_pattern.match(candidate):
                    return candidate

        return None

    try:
        page.goto(url, wait_until="networkidle", timeout=30000)

        for _ in range(20):
            text = page.locator("body").inner_text()
            lines = [line.strip() for line in text.splitlines() if line.strip()]

            coach = extract_from_lines(lines)

            if coach:
                return coach

            page.wait_for_timeout(1000)

        return None

    finally:
        page.close()


def get_ninetyminut_coach(url):
    html = None

    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            response = urlopen(request, timeout=30)
            html = response.read().decode("iso-8859-2", errors="ignore")
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)

    index = html.find("Trener:")
    if index == -1:
        return None, None

    fragment = html[index:index + 500]

    pattern = r"<b>(.*?)</b>\s*\(od\s*(.*?)\)"
    matches = re.findall(pattern, fragment)

    if matches:
        coach_name, change_date = matches[-1]

        coach_name = coach_name.strip()
        change_date = change_date.strip()

        coach_name = re.sub(r"\s+[A-Z]{3}(\/[A-Z]{3})*$", "", coach_name).strip()

        return coach_name, change_date

    return None, None


def process_club(browser, club, last_checked):
    league = club["league"]
    group = club.get("group", "")
    club_name = club["club"]
    superscore_url = club["superscore_url"]
    ninetyminut_url = club["ninetyminut_url"]

    try:
        superscore_coach = get_superscore_coach(browser, superscore_url)
    except Exception as e:
        superscore_coach = None
        print(f"SuperScore error for {club_name}: {e}")

    try:
        ninetyminut_coach, change_date = get_ninetyminut_coach(ninetyminut_url)
    except Exception as e:
        ninetyminut_coach = None
        change_date = None
        print(f"90minut error for {club_name}: {e}")

    if superscore_coach is None or ninetyminut_coach is None:
        is_difference = False
        result = "UNKNOWN"
    else:
        is_difference = normalize_name(superscore_coach) != normalize_name(ninetyminut_coach)
        result = "DIFFERENCE" if is_difference else "MATCH"

    return {
        "league": league,
        "group": group,
        "club": club_name,
        "superscore_coach": superscore_coach,
        "ninetyminut_coach": ninetyminut_coach,
        "previous_90minut_coach": "",
        "change_date": change_date,
        "last_checked": last_checked,
        "is_difference": is_difference,
        "result": result,
    }


def print_result(row):
    print("-" * 40)
    print("Club:", row["club"])
    print("SuperScore coach:", row["superscore_coach"])
    print("90minut coach:", row["ninetyminut_coach"])
    print("90minut change date:", row["change_date"])
    print("Result:", row["result"])


parser = argparse.ArgumentParser()
parser.add_argument("--league", default="all")
args = parser.parse_args()

selected_league = args.league.lower()

last_checked = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d %H:%M:%S")

with open("clubs.csv", "r", encoding="utf-8") as file:
    clubs = list(csv.DictReader(file))

if selected_league != "all":
    clubs = [
        club for club in clubs
        if club["league"].lower() == selected_league
    ]


results = []

def process_club_worker(club, last_checked):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        try:
            return process_club(browser, club, last_checked)
        finally:
            browser.close()


with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
    futures = [
        executor.submit(process_club_worker, club, last_checked)
        for club in clubs
    ]

    for future in as_completed(futures):
        row = future.result()
        results.append(row)
        print_result(row)


results.sort(key=lambda x: (x["league"], x["group"], x["club"]))


if selected_league != "all" and os.path.exists("results.csv"):
    with open("results.csv", "r", encoding="utf-8") as file:
        existing_results = list(csv.DictReader(file))

    existing_results = [
        row for row in existing_results
        if row["league"].lower() != selected_league
    ]

    results = existing_results + results

results.sort(key=lambda x: (x["league"], x["group"], x["club"]))


with open("results.csv", "w", encoding="utf-8", newline="") as file:
    fieldnames = [
        "league",
        "group",
        "club",
        "superscore_coach",
        "ninetyminut_coach",
        "previous_90minut_coach",
        "change_date",
        "last_checked",
        "is_difference",
        "result",
    ]

    writer = csv.DictWriter(file, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(results)


end_time = time.time()

print()
print("Saved results to results.csv")
print("Clubs checked:", len(results))
print("Execution time:", round(end_time - start_time, 2), "seconds")