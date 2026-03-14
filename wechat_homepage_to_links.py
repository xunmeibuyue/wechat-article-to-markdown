from __future__ import annotations

import argparse
import asyncio
import html
import json
import random
import re
import sys
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup
from camoufox.async_api import AsyncCamoufox


OUTPUT_DIR = Path.cwd() / "output"


def _force_https_mp_weixin(url: str) -> str:
    """
    规范化 mp.weixin.qq.com 链接：
    - 强制 https
    - 去掉默认端口
    - 保留 query/fragment（便于直接打开）
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    host = (parsed.hostname or "").lower()
    if host != "mp.weixin.qq.com":
        return url

    return urlunparse(
        (
            "https",
            "mp.weixin.qq.com",
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def _is_wechat_article_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    host = (parsed.hostname or "").lower()
    if host != "mp.weixin.qq.com":
        return False

    # 文章页通常是 /s 或 /s/<id>
    return parsed.path == "/s" or parsed.path.startswith("/s/")


def _dedupe_keep_order(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def normalize_wechat_url(raw: str) -> str:
    """
    尽可能“宽容”地规范化用户输入的 mp.weixin.qq.com URL。

    典型问题：
    - 终端/输入法在粘贴 URL 时插入反斜杠转义（如 `\\&`、`\\?`），导致参数被污染
    - 从网页复制包含 HTML 实体（如 `&amp;`）
    - 有些链接以 http 开头，但 mp.weixin.qq.com 实际稳定使用 https
    """
    s = str(raw or "").strip()
    if not s:
        return s

    # 去掉常见包裹（有些场景会把引号也一起复制进来）
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()
    if s.startswith("<") and s.endswith(">"):
        s = s[1:-1].strip()

    # 反斜杠转义清理：
    # - zsh 的 url-quote-magic / 终端“安全粘贴”有时会把 `&` 等符号转义成 `\\&`
    # - JSON/字符串转义也可能出现 `\\/`、`https\\://` 这类形式
    s = re.sub(r"\\+([:/&?=#%])", r"\1", s)

    # HTML 实体解码（`&amp;` -> `&`）
    s = html.unescape(s)

    # 允许省略 scheme 的复制结果
    if s.startswith("mp.weixin.qq.com/") or s.startswith("//mp.weixin.qq.com/"):
        s = "https://" + s.lstrip("/")

    parsed = urlparse(s)
    if parsed.scheme in ("http", "https") and (parsed.netloc or parsed.hostname):
        host = (parsed.hostname or "").lower()
        if host == "mp.weixin.qq.com":
            s = urlunparse(
                (
                    "https",
                    "mp.weixin.qq.com",
                    parsed.path,
                    parsed.params,
                    parsed.query,
                    parsed.fragment,
                )
            )

    return s


def _normalize_link(url: str) -> str:
    cleaned = normalize_wechat_url(url)
    cleaned = _force_https_mp_weixin(cleaned)
    return cleaned


def _extract_query_params(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    params: dict[str, str] = {}
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        # 保留最后一次出现的值（微信链接通常不会重复，但稳妥一些）
        params[key] = value
    return params


def _build_homepage_api_url(homepage_url: str, *, begin: int) -> str:
    """
    构造请求更多推文的接口 URL。

    观察到微信主页会发起类似请求：
      /mp/homepage?...&begin=0&action=appmsg_list&f=json&r=...&appmsg_token=

    关键点：
    - 该接口需要 **POST**（GET 会返回另一种结构：data.html 的 HTML 片段）
    - 必须带上 homepage 原有的 query 参数（至少 __biz/hid/sn）
    """
    parsed = urlparse(homepage_url)
    base_params = _extract_query_params(homepage_url)

    # 去掉可能冲突的分页/动作参数，统一由我们写入
    for k in ("begin", "action", "f", "r", "appmsg_token"):
        base_params.pop(k, None)

    base_params.update(
        {
            "begin": str(begin),
            "action": "appmsg_list",
            "f": "json",
            # 随机数参数是页面里常见的写法（可能用于缓存穿透）
            "r": str(random.random()),
            "appmsg_token": "",
        }
    )

    query = urlencode(base_params)
    return urlunparse(("https", "mp.weixin.qq.com", parsed.path, "", query, ""))


async def _fetch_all_article_links(
    page,
    homepage_url: str,
    *,
    max_items: int = 500,
    per_request_sleep: float = 0.2,
) -> list[str]:
    """
    通过 /mp/homepage?action=appmsg_list 接口分页拉取“全部推文链接”。

    之所以不只靠 DOM 滚动：
    - 主页的 DOM 通常只渲染一部分（例如 11 条）
    - 页面内部会通过 XHR 拉更多列表，但 headless/风控场景下 DOM 不一定会 append
    - 直接调用接口（POST）更稳定，可拿到完整列表
    """
    referer = homepage_url.split("#", 1)[0]

    links: list[str] = []
    begin = 0
    has_more = True

    while has_more and len(links) < max_items:
        api_url = _build_homepage_api_url(homepage_url, begin=begin)
        resp = await page.request.post(
            api_url,
            headers={
                "Referer": referer,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
        )
        data = json.loads(await resp.text())

        base_resp = data.get("base_resp") or {}
        if base_resp.get("ret") not in (0, "0", None):
            raise RuntimeError(f"接口返回异常: base_resp.ret={base_resp.get('ret')}")

        appmsg_list = data.get("appmsg_list") or []
        page_links = []
        for item in appmsg_list:
            link = item.get("link")
            if isinstance(link, str) and link.strip():
                page_links.append(_normalize_link(link))

        links = _dedupe_keep_order([*links, *page_links])

        has_more = bool(data.get("has_more"))
        if not appmsg_list:
            break

        begin += len(appmsg_list)
        await asyncio.sleep(per_request_sleep)

    return [u for u in links if _is_wechat_article_url(u)]


def _filter_page_links(raw_hrefs: Iterable[str]) -> list[str]:
    cleaned = []
    for href in raw_hrefs:
        if not isinstance(href, str):
            continue
        s = href.strip()
        if not s or s.startswith("javascript:"):
            continue
        cleaned.append(_normalize_link(s))
    return _dedupe_keep_order(cleaned)


async def _extract_homepage(
    url: str,
    *,
    headless: bool = True,
    timeout_ms: int = 60_000,
    max_items: int = 500,
    debug: bool = False,
) -> tuple[str, list[str], list[str], list[str]]:
    url = normalize_wechat_url(url)

    if not url.startswith("https://mp.weixin.qq.com/"):
        raise ValueError("请输入 mp.weixin.qq.com 域名下的链接")

    debug_html = ""
    debug_screenshot: bytes | None = None

    async with AsyncCamoufox(headless=headless) as browser:
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

        # 主页文章列表容器常见为 #appmsgList；有时会被风控/验证码替换，因此做 best-effort 等待
        try:
            await page.wait_for_selector("#appmsgList, a.list_item.js_post", timeout=10_000)
        except Exception:
            pass

        page_title = (await page.title()).strip()

        page_hrefs = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(el => el.href).filter(Boolean)",
        )
        page_links = _filter_page_links(page_hrefs)

        article_links = await _fetch_all_article_links(page, url, max_items=max_items)

        # 额外从 HTML 里抓一次标题（有些页面 title 可能不是列表头）
        debug_html = await page.content()
        if debug:
            try:
                debug_screenshot = await page.screenshot(full_page=True)
            except Exception:
                debug_screenshot = None

    soup = BeautifulSoup(debug_html, "html.parser")
    header = soup.select_one(".rich_media_title")
    visible_title = header.get_text(strip=True) if header else ""
    final_title = visible_title or page_title

    if debug:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / "homepage_debug.html").write_text(debug_html, encoding="utf-8")
        if debug_screenshot:
            (OUTPUT_DIR / "homepage_debug.png").write_bytes(debug_screenshot)

    all_links = _dedupe_keep_order([*page_links, *article_links])
    return final_title, article_links, page_links, all_links


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="wechat-homepage-to-links",
        description="提取微信公众号主页(mp/homepage)的标题与链接（含完整推文列表）",
    )
    parser.add_argument("url", help="微信公众号主页 URL（例如 https://mp.weixin.qq.com/mp/homepage?...）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（便于程序二次处理）")
    parser.add_argument("--timeout-ms", type=int, default=60_000, help="页面打开超时（毫秒）")
    parser.add_argument("--max-items", type=int, default=500, help="最多拉取推文条数（防止异常循环）")
    parser.add_argument("--headed", action="store_true", help="使用有头模式（便于调试）")
    parser.add_argument("--debug", action="store_true", help="保存 debug HTML/截图到 output/ 目录")
    args = parser.parse_args()

    try:
        title, article_links, page_links, all_links = asyncio.run(
            _extract_homepage(
                args.url,
                headless=not args.headed,
                timeout_ms=args.timeout_ms,
                max_items=max(1, args.max_items),
                debug=args.debug,
            )
        )
    except Exception as e:
        print(f"❌ 提取失败: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        payload = {
            "title": title,
            "links": all_links,
            "article_links": article_links,
            "page_links": page_links,
            "all_links": all_links,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"📄 页面标题: {title}")
    print(f"🔗 推文链接数: {len(article_links)}")
    print(f"🔗 页面 a[href] 链接数: {len(page_links)}")
    print(f"🔗 合并去重链接数: {len(all_links)}")

    for i, link in enumerate(all_links, start=1):
        print(f"{i:02d}. {link}")


if __name__ == "__main__":
    main()
