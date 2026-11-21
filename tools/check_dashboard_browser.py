"""Local-only dashboard smoke test; requires Playwright and installed Edge."""
import asyncio
import json
import secrets
import sys
from pathlib import Path

from aiohttp import web
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from signal_server import application


async def main():
    token = secrets.token_urlsafe(32)
    runner = web.AppRunner(application({}, admin_token=token))
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        origin = f"http://127.0.0.1:{runner.addresses[0][1]}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="msedge")
            page = await browser.new_page(viewport={"width": 1440, "height": 960})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(origin + "/admin")
            await page.locator("#token").fill(token)
            await page.locator("#login-form button").click()
            await expect(page.locator("#dashboard")).to_be_visible()
            for view in ("overview", "nodes", "settings"):
                await page.locator(f'nav a[href="#{view}"]').click()
                await expect(page.locator("[data-view]:visible")).to_have_count(1)
                await expect(page.locator("[data-view]:visible")).to_have_attribute("data-view", view)
            await page.locator(".import-panel summary").click()
            items = [{"name": "测试节点 " + label, "url": f"https://{label}.invalid"}
                     for label in ("alpha", "beta")]
            await page.locator("#import-file").set_input_files({
                "name": "nodes.json", "mimeType": "application/json",
                "buffer": json.dumps(items).encode()})
            async with page.expect_response(lambda r: r.url.endswith("/admin-api/nodes")
                                             and r.request.method == "POST") as event:
                await page.locator("#import-submit").click()
            result = await (await event.value).json()
            for node, cpu in zip(result["registered"], (25, 95), strict=True):
                response = await page.request.post(origin + f"/cluster/{node['id']}/report",
                    headers={"Authorization": "Bearer " + node["token"]},
                    data=dict(cpu_percent=cpu, cpu_count=2, memory_total=1000000000,
                              memory_used=200000000, disk_total=10000000000,
                              disk_used=1000000000, network_bps=1000))
                assert response.ok, response.status
            await page.locator("#dismiss-credential").click()
            await page.locator('nav a[href="#nodes"]').click()
            await page.locator("#refresh").click()
            await page.locator(".server-record summary").first.click()
            await page.get_by_role("button", name="调整策略").first.click()
            await expect(page.locator("#capacity")).to_be_visible()
            await page.go_back()
            await expect(page.locator("#node-list")).to_be_visible()
            await page.reload()
            await page.locator("#token").fill(token)
            await page.locator("#login-form button").click()
            await expect(page.locator("#node-list")).to_be_visible()
            await page.locator('nav a[href="#overview"]').click()
            await expect(page.locator("#totals")).to_contain_text("60")
            await expect(page.locator("#alerts")).to_contain_text("beta")
            for name, width, height in (("desktop", 1440, 960), ("mobile", 390, 844)):
                await page.set_viewport_size({"width": width, "height": height})
                await page.evaluate("window.scrollTo(0,0)")
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), name
                (ROOT / "build" / "evidence").mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=ROOT / "build" / "evidence" / f"dashboard-{name}.png")
            assert not errors, errors
            await browser.close()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
