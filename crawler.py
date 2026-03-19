#!/usr/bin/env python3
"""
Context Engine — Async BFS web crawler module
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("context-engine.crawler")

# Tags to remove before text extraction
STRIP_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript", "svg", "iframe"}

CHECKPOINT_SAVE_INTERVAL = 10  # Save checkpoint every N pages


def extract_text(html: str) -> str:
    """Extract clean text from HTML, preferring main/article content."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove unwanted tags
    for tag in soup.find_all(STRIP_TAGS):
        tag.decompose()

    # Try main content areas first
    content = soup.find("main") or soup.find("article") or soup.find("body")
    if content is None:
        return ""

    text = content.get_text(separator="\n", strip=True)
    # Collapse multiple blank lines
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


def save_checkpoint(path: Path, visited: set, queue: list) -> None:
    """Persist crawl state to disk so it can be resumed later."""
    data = {"visited": list(visited), "queue": queue}
    path.write_text(json.dumps(data))


def load_checkpoint(path: Path) -> tuple[set, list]:
    """Load crawl state from disk. Returns (visited, queue) or (empty set, empty list)."""
    if not path.exists():
        return set(), []
    try:
        data = json.loads(path.read_text())
        return set(data.get("visited", [])), data.get("queue", [])
    except Exception as e:
        log.warning("Failed to load checkpoint %s: %s — starting fresh", path, e)
        return set(), []


async def crawl_site(
    url: str,
    coll_name: str,
    task_state: dict,
    max_pages: int = 200,
    path_prefix: str | None = None,
    embed_fn=None,
    add_fn=None,
    checkpoint_path: Path | None = None,
):
    """BFS crawl a site, chunking and embedding each page into a collection.

    If checkpoint_path is provided, crawl state is saved periodically and can
    be resumed across server restarts.
    """
    parsed = urlparse(url)
    domain = parsed.netloc
    if path_prefix is None:
        # Default to the path of the starting URL's parent
        path_prefix = "/".join(parsed.path.rstrip("/").split("/")[:-1]) + "/" if "/" in parsed.path.rstrip("/") else "/"

    sem = asyncio.Semaphore(5)

    # Load checkpoint if it exists
    if checkpoint_path:
        visited, queue = load_checkpoint(checkpoint_path)
        if visited:
            pages_crawled = len(visited)
            log.info(
                "Resuming crawl from checkpoint: %d already visited, %d queued",
                pages_crawled, len(queue),
            )
            task_state["resumed"] = True
            task_state["pages_crawled"] = pages_crawled
        else:
            visited = set()
            queue = [url.rstrip("/")]
            pages_crawled = 0
    else:
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
            from server import chunk_text
            chunks = chunk_text(text, size=400)
            for chunk in chunks:
                add_fn(chunk, coll_name, source=current_url)

            pages_crawled += 1
            task_state["pages_crawled"] = pages_crawled
            log.info("Crawled [%d/%d]: %s (%d chunks)", pages_crawled, max_pages, current_url, len(chunks))

            # Extract and queue new links
            new_links = extract_links(html, current_url, domain, path_prefix)
            for link in new_links:
                if link not in visited:
                    queue.append(link)

            # Save checkpoint periodically
            if checkpoint_path and pages_crawled % CHECKPOINT_SAVE_INTERVAL == 0:
                save_checkpoint(checkpoint_path, visited, queue)
                log.debug("Checkpoint saved at %d pages", pages_crawled)

            # Polite delay
            await asyncio.sleep(0.5)

    # Optimize after crawl
    try:
        from server import get_collection
        coll = get_collection(coll_name)
        coll.optimize()
    except Exception:
        pass

    # Delete checkpoint on successful completion
    if checkpoint_path and checkpoint_path.exists():
        checkpoint_path.unlink()
        log.info("Checkpoint cleared for completed crawl")

    task_state["pages_total"] = pages_crawled
    log.info("Crawl complete: %d pages indexed", pages_crawled)
