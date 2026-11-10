"""Shopee product identity: what a product URL says about `<shopId>_<itemId>`.

MeoScout sends `platform_product_id` as `<shopId>_<itemId>` (contract 2.0.0) and, separately,
the product URL as it stored it. Both name the same product, so ingest cross-checks them
instead of trusting either blindly. Only identifiers that the URL states literally are read
(`-i.<shop>.<item>`, `/product/<shop>/<item>`, `itemid` / `shopid` query parameters); nothing
is guessed. Mirrors MeoScout `parseShopeeProductId`, but reads the path and query separately
so a stray substring elsewhere in the URL cannot match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

PLATFORM_PRODUCT_ID_RE = re.compile(r"^(\d+)_(\d+)$")
_SLUG_RE = re.compile(r"-i\.(\d+)\.(\d+)/?$")
_PRODUCT_PATH_RE = re.compile(r"^/product/(\d+)/(\d+)/?$")
_ITEM_PARAMS = ("itemid", "item_id")
_SHOP_PARAMS = ("shopid", "shop_id")
_DIGITS_RE = re.compile(r"^\d+$")


@dataclass(frozen=True, slots=True)
class ShopeeIds:
    item_id: str
    shop_id: str | None = None

    @property
    def platform_product_id(self) -> str | None:
        return f"{self.shop_id}_{self.item_id}" if self.shop_id else None

    def __str__(self) -> str:
        return self.platform_product_id or f"item {self.item_id}"


class AmbiguousShopeeUrlError(ValueError):
    """The URL names two different products (e.g. path and query disagree)."""


def is_shopee_vn_host(hostname: str | None) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host == "shopee.vn" or host.endswith(".shopee.vn")


def split_platform_product_id(value: str) -> ShopeeIds | None:
    match = PLATFORM_PRODUCT_ID_RE.match(value)
    return ShopeeIds(item_id=match.group(2), shop_id=match.group(1)) if match else None


def parse_shopee_ids(url: str) -> ShopeeIds | None:
    """The ids a Shopee product URL states, or None when it states none.

    Raises `AmbiguousShopeeUrlError` when two sources in the same URL disagree."""
    parts = urlsplit(url.strip())
    found: list[ShopeeIds] = []
    for pattern in (_SLUG_RE, _PRODUCT_PATH_RE):
        match = pattern.search(parts.path)
        if match:
            found.append(ShopeeIds(item_id=match.group(2), shop_id=match.group(1)))
    params = {k.lower(): v for k, v in parse_qsl(parts.query, keep_blank_values=False)}
    query_shops = [params[k] for k in _SHOP_PARAMS if k in params and _DIGITS_RE.match(params[k])]
    for key in _ITEM_PARAMS:
        item = params.get(key)
        if item is not None and _DIGITS_RE.match(item):
            found.extend(ShopeeIds(item_id=item, shop_id=shop) for shop in query_shops or [None])
    if not found:
        return None
    items = {ids.item_id for ids in found}
    shops = {ids.shop_id for ids in found if ids.shop_id}
    if len(items) > 1 or len(shops) > 1:
        msg = "the URL names more than one product"
        raise AmbiguousShopeeUrlError(msg)
    return ShopeeIds(item_id=items.pop(), shop_id=shops.pop() if shops else None)
