import asyncio
import os
import platform
import re

# import subprocess
from abc import ABC, abstractmethod
from playwright._impl._errors import Error
import requests
import streamlit as st
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError
from playwright_stealth import stealth_async

try:
    st.set_page_config(page_title="关键字排名检查器", page_icon=None, layout="centered")
except st.errors.StreamlitSetPageConfigMustBeFirstCommandError:
    pass


def install_playwright():
    if "playwright_installed" not in st.session_state:
        os.system("playwright install")
        st.session_state["playwright_installed"] = True
        st.toast("Playwright installed.")
    else:
        st.toast("Playwright ready.")


def calculate_progress(value, min_val, max_val):
    """
    Calculate progress percentage (1-100) from value in any range.
    Handles edge cases and ensures result stays within 1-100.
    """
    # Ensure value is within bounds
    clamped = max(min_val, min(max_val, value))

    # Avoid division by zero if range is invalid
    if max_val == min_val:
        return 100 if value >= max_val else 1

    # Calculate progress
    progress = 1 + ((clamped - min_val) * 99) / (max_val - min_val)
    return max(1, min(100, round(progress)))


if platform.system() != "Windows":
    install_playwright()


N_PAGES_TO_CHECK = 50


class Scraper(ABC):
    @abstractmethod
    def scrape(self):
        pass


class GoogleScraper(Scraper):
    base = "https://www.google.com/"

    def __init__(self, browser):
        self.browser = browser

    async def scrape(
        self,
        kw: str,
        domain: str,
        progress,
        n_pages: int = 100,  # Google does not serve more than 1000 results for any query.
    ) -> tuple[int, list]:
        page = await self.browser.new_page()

        await stealth_async(page)

        await page.goto(self.base, timeout=60000)

        await page.locator("textarea").first.fill(kw)
        await page.locator("textarea").first.press("Enter")

        hrefs = []
        for pn in range(n_pages):
            progress.progress(
                calculate_progress(pn + 1, 1, n_pages),
                text=f"检查第{pn + 1}/{n_pages}页 | Checking page {pn + 1}/{n_pages}...",
            )

            await page.wait_for_selector("a:has(h3)", timeout=60000)

            # Find all <a> tags that contain <h3> tags using Playwright
            a_tags = await page.locator("a:has(h3)").all()

            for a_tag in a_tags:
                href = await a_tag.get_attribute("href")
                text = await a_tag.text_content()

                if href:
                    hrefs.append({"标题": text, "URL": href})

                    if domain in href:
                        # Add CSS to highlight the element
                        parent = a_tag
                        for _ in range(8):  # include N surrounding elements
                            parent = parent.locator(
                                ".."
                            )  # ".." selects the parent element
                        await parent.evaluate("""element => {
                            element.style.border = '4px solid red';
                            element.style.boxShadow = '0 0 8px rgba(255, 0, 0, 0.5)';
                        }""")

                        await page.screenshot(path="response.png", full_page=True)

                        return len(hrefs), hrefs

            # Go to next page
            try:
                # Wait for element to be ready
                next_button = page.locator("a#pnnext")

                # Scroll into view
                await next_button.scroll_into_view_if_needed()

                # Click with retries
                await next_button.dispatch_event("click")
            except TimeoutError:  # End of results
                print("End of results")
                break

        return 0, hrefs


class BaiduScraper(Scraper):
    base = "https://www.baidu.com/"

    def __init__(self, browser):
        self.browser = browser

    async def scrape(
        self, kw: str, domain: str, progress, n_pages: int = 100
    ) -> tuple[int, list]:
        page = await self.browser.new_page()

        await page.goto(self.base)

        await page.locator("#kw").fill(kw)
        await page.locator("#su").click()

        await page.wait_for_selector("a:has(em)")

        hrefs = []
        p = await self.browser.new_page()
        for pn in range(n_pages):
            progress.progress(
                calculate_progress(pn + 1, 1, n_pages),
                text=f"检查第{pn + 1}/{n_pages}页 | Checking page {pn + 1}/{n_pages}...",
            )

            url = f"{page.url}&pn={pn * 10}"
            print(url)

            await p.goto(url)

            # End of results
            soup = BeautifulSoup(await p.content(), "html.parser")
            if pn > 0:
                # Find all <strong> > <span> where class starts with "page-item"
                matching_spans = soup.select('strong > span[class^="page-item"]')
                # Check if any have text "1"
                has_text_1 = any(
                    span.get_text(strip=True) == "1" for span in matching_spans
                )

                if has_text_1:
                    print("End of results")
                    break

            # Find all <a> tags that contain <em> tags using Playwright
            a_tags = await page.locator("a:has(em)").all()

            for a_tag in a_tags:
                href = await a_tag.get_attribute("href")
                text = await a_tag.text_content()

                try:
                    response = requests.get(href, allow_redirects=True)
                    final_url = response.url
                    response.close()
                    hrefs.append({"标题": text, "URL": final_url})
                    if domain in final_url:
                        # Add CSS to highlight the element
                        parent = a_tag
                        for _ in range(5):  # include N surrounding elements
                            parent = parent.locator(
                                ".."
                            )  # ".." selects the parent element
                        await parent.evaluate("""element => {
                            element.style.border = '4px solid red';
                            element.style.boxShadow = '0 0 8px rgba(255, 0, 0, 0.5)';
                        }""")

                        await page.screenshot(path="response.png", full_page=True)

                        return len(hrefs), hrefs
                except requests.exceptions.ConnectionError as e:
                    print(e)
                    hrefs.append({"标题": text, "URL": href})
                except requests.exceptions.MissingSchema as e:
                    print(e)
                    hrefs.append({"标题": text, "URL": href})

            pn = pn + 10

        return 0, hrefs


AVAILABLE_SEARCH_ENGINES = {
    "百度": BaiduScraper,
    "谷歌": GoogleScraper,
}


async def run(KW, domain, se, n_pages, progress):
    async with async_playwright() as playwright:
        browser = await playwright.firefox.launch(headless=True)
        bs = AVAILABLE_SEARCH_ENGINES[se](browser)
        try:
            response = await bs.scrape(KW, domain, progress, n_pages)
        except Error:
            st.error("网络错误。请重试 | Network error. Please retry")
        await browser.close()

        st.stop()

        return response


if __name__ == "__main__":
    st.header("关键字排名检查器")
    st.subheader(
        "Keyword Rank Checker",
    )

    st.logo("logo_hover.webp")

    response = None

    with st.form("krc", enter_to_submit=False):
        col1, col2 = st.columns([3, 2])
        with col1:
            se = st.radio(
                "选择搜索引擎 | Select search engine",
                list(AVAILABLE_SEARCH_ENGINES.keys()),
                horizontal=True,
            )
        with col2:
            n_pages = st.number_input(
                "要检查多少页 | How many pages to check",
                1,
                1000,
                100,
                10,
            )
        keyword = st.text_input(
            "输入关键字 | Enter keyword", "苏州空谷网络科技有限公司"
        )
        domain = st.text_input("输入域名 | Enter domain", "kgu.cn")

        submitted = st.form_submit_button(
            "运行 | Run", type="primary", use_container_width=True
        )

        if submitted:
            errors = []
            # Validate keyword
            if not keyword:
                errors.append("关键字是必需的 | Keyword is required")
            # Validate domain
            if not domain:
                errors.append("域名是必需的 | Domain is required")
            elif not re.match(r"^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$", domain):
                errors.append(
                    "域名必须与格式匹配: kgu.cn, sub.domain.com | Domain must match the format: kgu.cn, sub.domain.com"
                )
            # Validate n_pages
            if not 1 <= n_pages <= 1000:
                errors.append(
                    "页数必须在1-1000的范围内 | The number of pages must be in the range 1-1000"
                )
            if errors:
                for error in errors:
                    st.error(error)
                st.stop()

            progress = st.progress(0, text="请稍候 | Please wait...")

            if platform.system() == "Windows":
                loop = asyncio.ProactorEventLoop()
            else:
                loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            response = loop.run_until_complete(
                run(keyword, domain, se, n_pages, progress)
            )

            progress.empty()

    if response and response[0] > 0:
        st.balloons()
        st.metric(label="域名排名 | Domain rank", value=response[0], border=True)
        st.image("response.png")
    elif response and response[0] == 0:
        st.warning(
            f"在前{n_pages}页中找不到域名 | Domain not found in the first {n_pages} page(s)"
        )

        data = [{**{"排名": i + 1}, **item} for i, item in enumerate(response[1])]
        st.dataframe(data, height=300)
