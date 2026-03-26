#!/usr/bin/env python3
"""
Context Engine — Async BFS web crawler module
"""

import asyncio
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("context-engine.crawler")

# Tags to remove before text extraction
STRIP_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript", "svg", "iframe"}

def extract_text(html: str) -> str:
    """Extract clean text from HTML, preserving heading structure as markdown-style markers.
    Headings become double-newline separated sections, which the recursive splitter
    uses as natural chunk boundaries.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(STRIP_TAGS):
        tag.decompose()

    content = soup.find("main") or soup.find("article") or soup.find("body")
    if content is None:
        return ""

    # Replace headings with markdown-style markers so the recursive splitter
    # treats them as natural \n\n split points.
    # Use a sentinel prefix so get_text doesn't eat the newlines.
    HEADING_SENTINEL = "\x00HEADING\x00"
    for heading in content.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        level = int(heading.name[1])
        prefix = "#" * level
        heading_text = heading.get_text(strip=True)
        if heading_text:
            heading.replace_with(f"{HEADING_SENTINEL}{prefix} {heading_text}{HEADING_SENTINEL}")

    # Preserve code block formatting
    CODE_SENTINEL = "\x00CODE\x00"
    for pre in content.find_all("pre"):
        code_text = pre.get_text()
        pre.replace_with(f"{CODE_SENTINEL}```\n{code_text.strip()}\n```{CODE_SENTINEL}")

    text = content.get_text(separator="\n", strip=True)

    # Expand sentinels into double-newline separated blocks
    text = re.sub(re.escape(HEADING_SENTINEL) + r"(.+?)" + re.escape(HEADING_SENTINEL),
                  r"\n\n\1\n\n", text)
    text = re.sub(re.escape(CODE_SENTINEL) + r"(.+?)" + re.escape(CODE_SENTINEL),
                  r"\n\n\1\n\n", text, flags=re.DOTALL)

    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def extract_links(html: str, base_url: str, domain: str, path_prefix: str | None) -> list[str]:
    """Extract same-domain links, optionally filtered by path prefix."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Skip anchors, javascript, mailto
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        # Same domain only
        if parsed.netloc != domain:
            continue
        # Strip fragment
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        # Remove trailing slash for consistency
        clean = clean.rstrip("/")
        # Path prefix filter
        if path_prefix and not parsed.path.startswith(path_prefix):
            continue
        links.append(clean)
    return links

async def crawl_site(
    url: str,
    collection,
    task_state: dict,
    max_pages: int = 200,
    path_prefix: str | None = None,
    embed_fn=None,
    add_fn=None,
    chunk_fn=None,
):
    """BFS crawl a site, chunking and embedding each page into a collection."""
    if chunk_fn is None:
        raise ValueError("crawl_site: chunk_fn parameter cannot be None")
    if add_fn is None:
        raise ValueError("crawl_site: add_fn parameter cannot be None")
    parsed = urlparse(url)
    domain = parsed.netloc
    if path_prefix is None:
        # Default to the path of the starting URL's parent
        path_prefix = "/".join(parsed.path.rstrip("/").split("/")[:-1]) + "/" if "/" in parsed.path.rstrip("/") else "/"

    sem = asyncio.Semaphore(5)
    visited = set()
    queue = [url.rstrip("/")]
    pages_crawled = 0

    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "ContextEngine/1.0 (local indexer)"},
    ) as client:
        while queue and pages_crawled < max_pages:
            current_url = queue.pop(0)
            if current_url in visited:
                continue
            visited.add(current_url)

            async with sem:
                try:
                    resp = await client.get(current_url)
                    if resp.status_code != 200:
                        continue
                    content_type = resp.headers.get("content-type", "")
                    if "text/html" not in content_type:
                        continue
                    html = resp.text
                except Exception as e:
                    log.warning("Failed to fetch %s: %s", current_url, e)
                    continue

            text = extract_text(html)
            if not text or len(text) < 50:
                continue

            # Chunk and add
            chunks = chunk_fn(text)
            for chunk in chunks:
                add_fn(chunk, collection, source=current_url)

            pages_crawled += 1
            task_state["pages_crawled"] = pages_crawled
            log.info("Crawled [%d/%d]: %s (%d chunks)", pages_crawled, max_pages, current_url, len(chunks))

            # Extract and queue new links
            new_links = extract_links(html, current_url, domain, path_prefix)
            for link in new_links:
                if link not in visited:
                    queue.append(link)

            # Polite delay
            await asyncio.sleep(0.5)

    # Optimize after crawl
    try:
        collection.optimize()
    except Exception:
        pass

    task_state["pages_total"] = pages_crawled
    log.info("Crawl complete: %d pages indexed", pages_crawled)
