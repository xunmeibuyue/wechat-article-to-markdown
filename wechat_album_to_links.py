from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from camoufox.async_api import AsyncCamoufox

from wechat_homepage_to_links import (
    _dedupe_keep_order,
    _extract_query_params,
    _is_wechat_article_url,
    _normalize_link,
    normalize_wechat_url,
)

OUTPUT_DIR = Path.cwd() / "output"


def _build_album_api_url(
    album_url: str,
    *,
    begin_msgid: str = "",
    begin_itemidx: str = "",
    count: int = 10,
) -> str:
    """
    构造合集分页 API URL。

    合集页的分页接口为 GET 请求，关键参数：
      action=getalbum&album_id=...&count=10
      &begin_msgid=<上一页最后一条的 msgid>
      &begin_itemidx=<上一页最后一条的 itemidx>
      &f=json
    """
    parsed = urlparse(album_url)
    base_params = _extract_query_params(album_url)

    # 去掉可能冲突的分页/动作参数
    for k in ("action", "begin_msgid", "begin_itemidx", "count", "f", "r"):
        base_params.pop(k, None)

    base_params.update(
        {
            "action": "getalbum",
            "count": str(count),
            "f": "json",
            "r": str(random.random()),
        }
    )

    if begin_msgid:
        base_params["begin_msgid"] = begin_msgid
    if begin_itemidx:
        base_params["begin_itemidx"] = begin_itemidx

    query = urlencode(base_params)
    return urlunparse(("https", "mp.weixin.qq.com", parsed.path, "", query, ""))


async def _fetch_all_album_links(
    page,
    album_url: str,
    *,
    max_items: int = 500,
    per_request_sleep: float = 0.2,
) -> list[str]:
    """
    通过合集 API（GET /mp/appmsgalbum?action=getalbum）分页拉取全部文章链接。

    与主页 API 的关键差异：
    - HTTP 方法: GET（主页为 POST）
    - 分页: 游标式（begin_msgid + begin_itemidx），非偏移量
    - 继续标志: continue_flag (1/0)，非 has_more (bool)
    - 响应结构: getalbum_resp.article_list[].url
    """
    referer = album_url.split("#", 1)[0]

    links: list[str] = []
    begin_msgid = ""
    begin_itemidx = ""
    has_more = True

    while has_more and len(links) < max_items:
        api_url = _build_album_api_url(
            album_url,
            begin_msgid=begin_msgid,
            begin_itemidx=begin_itemidx,
        )
        resp = await page.request.get(
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

        album_resp = data.get("getalbum_resp") or {}
        article_list = album_resp.get("article_list") or []

        page_links = []
        for item in article_list:
            url = item.get("url")
            if isinstance(url, str) and url.strip():
                page_links.append(_normalize_link(url))

        links = _dedupe_keep_order([*links, *page_links])

        # 游标推进：用最后一条的 msgid / itemidx 作为下一页起点
        if article_list:
            last = article_list[-1]
            begin_msgid = str(last.get("msgid", ""))
            begin_itemidx = str(last.get("itemidx", ""))

        continue_flag = album_resp.get("continue_flag")
        has_more = str(continue_flag) == "1"

        if not article_list:
            break

        await asyncio.sleep(per_request_sleep)

    return [u for u in links if _is_wechat_article_url(u)]


async def _extract_album(
    url: str,
    *,
    headless: bool = True,
    timeout_ms: int = 60_000,
    max_items: int = 500,
    debug: bool = False,
) -> tuple[str, list[str]]:
    """
    完整的合集链接提取流程：
    1. 用 Camoufox 打开合集页（获取 cookie / 反检测上下文）
    2. 通过 API 分页拉取全部文章链接

    返回 (album_title, article_links)
    """
    url = normalize_wechat_url(url)

    if not url.startswith("https://mp.weixin.qq.com/"):
        raise ValueError("请输入 mp.weixin.qq.com 域名下的链接")

    async with AsyncCamoufox(headless=headless) as browser:
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

        # 合集页标题通常在 .album__author-name 或 .album_title
        try:
            await page.wait_for_selector(
                ".album__author-name, .album__title, .rich_media_title",
                timeout=10_000,
            )
        except Exception:
            pass

        page_title = (await page.title()).strip()

        # 尝试从 DOM 中提取更精确的合集标题
        album_title = ""
        for selector in (".album__title", ".rich_media_title"):
            try:
                el = await page.query_selector(selector)
                if el:
                    text = (await el.text_content() or "").strip()
                    if text:
                        album_title = text
                        break
            except Exception:
                pass

        final_title = album_title or page_title

        article_links = await _fetch_all_album_links(page, url, max_items=max_items)

        if debug:
            debug_html = await page.content()
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUTPUT_DIR / "album_debug.html").write_text(debug_html, encoding="utf-8")
            try:
                screenshot = await page.screenshot(full_page=True)
                (OUTPUT_DIR / "album_debug.png").write_bytes(screenshot)
            except Exception:
                pass

    return final_title, article_links


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="wechat-album-to-links",
        description="提取微信公众号合集页(mp/appmsgalbum)的标题与文章链接列表",
    )
    parser.add_argument(
        "url",
        help="微信公众号合集页 URL（例如 https://mp.weixin.qq.com/mp/appmsgalbum?...）",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（便于程序二次处理）")
    parser.add_argument("--timeout-ms", type=int, default=60_000, help="页面打开超时（毫秒）")
    parser.add_argument("--max-items", type=int, default=500, help="最多拉取文章条数（防止异常循环）")
    parser.add_argument("--headed", action="store_true", help="使用有头模式（便于调试）")
    parser.add_argument("--debug", action="store_true", help="保存 debug HTML/截图到 output/ 目录")
    args = parser.parse_args()

    try:
        title, article_links = asyncio.run(
            _extract_album(
                args.url,
                headless=not args.headed,
                timeout_ms=args.timeout_ms,
                max_items=max(1, args.max_items),
                debug=args.debug,
            )
        )
    except Exception as e:
        print(f"提取失败: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        payload = {
            "title": title,
            "article_links": article_links,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"页面标题: {title}")
    print(f"文章链接数: {len(article_links)}")

    for i, link in enumerate(article_links, start=1):
        print(f"{i:02d}. {link}")


if __name__ == "__main__":
    main()
