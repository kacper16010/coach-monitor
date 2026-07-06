from playwright.sync_api import sync_playwright

URL = "https://superscore.live/pl-PL/pilka-nozna/druzyny/gornik-zabrze/1482nb5k/sklad"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()

    def handle_response(response):
        url = response.url

        if "superscore.live" in url:
            print(response.status, url)

    page.on("response", handle_response)

    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(20000)

    browser.close()