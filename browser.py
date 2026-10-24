"""One reusable Chrome session (plain Selenium, automation flags hidden). Headless by default."""
import random
import time

import undetected_chromedriver as uc


class Browser:
    def __init__(self, headless: bool = True, version_main: int | None = 155,
                 proxy: str | None = None):
        # proxy example: "http://123.45.67.89:8080" (no username/password support)
        # Keep version_main aligned with the installed Chrome major version.
        options = uc.ChromeOptions()
        options.add_argument("--window-size=1280,900")
        options.add_argument("--lang=en-US")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-gpu")
        if proxy:
            options.add_argument(f"--proxy-server={proxy}")
        if headless:
            options.add_argument("--headless=new")
        self.driver = uc.Chrome(options=options, version_main=version_main, use_subprocess=True)
        if headless:
            self._hide_headless_user_agent()

    def _hide_headless_user_agent(self):
        """Headless Chrome announces itself as 'HeadlessChrome' - swap that out."""
        try:
            ua = self.driver.execute_script("return navigator.userAgent")
            self.driver.execute_cdp_cmd(
                "Network.setUserAgentOverride",
                {"userAgent": ua.replace("HeadlessChrome", "Chrome")},
            )
        except Exception:
            pass

    def get_html(self, url: str, min_wait: float = 3, max_wait: float = 6) -> str:
        self.driver.get(url)
        time.sleep(random.uniform(min_wait, max_wait))  # let the page render
        return self.driver.page_source

    @property
    def current_url(self) -> str:
        return self.driver.current_url

    def alive(self) -> bool:
        try:
            _ = self.driver.title
            return True
        except Exception:
            return False

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()