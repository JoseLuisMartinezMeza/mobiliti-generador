from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from typing import Any, Iterable
import hashlib
import json
import re
import urllib.parse
import urllib.request


TARKETTNET_ORIGIN = "https://www.tarkettnet.com.mx"
TARKETTNET_LOGIN_URL = f"{TARKETTNET_ORIGIN}/login.aspx"
TARKETTNET_HOME_URL = f"{TARKETTNET_ORIGIN}/vendas/home"
TARKETTNET_HOST = "www.tarkettnet.com.mx"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "MobilitiCatalogSync/1.0"
)
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
PAGE_SIZE = 60
DEFAULT_CATEGORY_URLS = (
    f"{TARKETTNET_ORIGIN}/vendas/alfombra/grain.html",
    f"{TARKETTNET_ORIGIN}/vendas/alfombra/essence.html",
    f"{TARKETTNET_ORIGIN}/vendas/alfombra/grezzo.html",
    f"{TARKETTNET_ORIGIN}/vendas/alfombra/defend.html",
    f"{TARKETTNET_ORIGIN}/vendas/alfombra/desert-airmaster.html",
    f"{TARKETTNET_ORIGIN}/vendas/vct/vinylasa.html",
    f"{TARKETTNET_ORIGIN}/vendas/lvt-comercial/ambienta.html",
    f"{TARKETTNET_ORIGIN}/vendas/lvt-comercial/aurea.html",
    f"{TARKETTNET_ORIGIN}/vendas/lvt-residencial/ambienta.html",
    f"{TARKETTNET_ORIGIN}/vendas/lvt-residencial/injoy.html",
    f"{TARKETTNET_ORIGIN}/vendas/lvt-residencial/aurea.html",
    f"{TARKETTNET_ORIGIN}/vendas/wall-base/johnsonite.html",
    f"{TARKETTNET_ORIGIN}/vendas/accesorios/accesorios.html",
)


def _clean_text(value: Any) -> str:
    return " ".join(str("" if value is None else value).split())


def _decimal(value: Any) -> Decimal:
    raw = _clean_text(value).replace("$", "").replace(",", "")
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValueError("Numero Tarkettnet invalido") from None
    if not number.is_finite() or number < 0:
        raise ValueError("Numero Tarkettnet invalido")
    return number


def _json_number(value: Decimal | int | float | str) -> int | float:
    number = value if isinstance(value, Decimal) else _decimal(value)
    if number == number.to_integral():
        return int(number)
    return float(number)


def _safe_category_url(value: str) -> str:
    url = urllib.parse.urljoin(TARKETTNET_ORIGIN, str(value or "").strip())
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != TARKETTNET_HOST:
        return ""
    if not parsed.path.startswith("/vendas/") or not parsed.path.endswith(".html"):
        return ""
    if len([part for part in parsed.path.split("/") if part]) < 3:
        return ""
    if parsed.query or parsed.fragment:
        return ""
    return urllib.parse.urlunparse(("https", TARKETTNET_HOST, parsed.path, "", "", ""))


def _safe_category_form_action(value: str) -> str:
    url = urllib.parse.urljoin(TARKETTNET_ORIGIN, str(value or "").strip())
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != TARKETTNET_HOST:
        return ""
    if parsed.path.lower() != "/cli_rep/categorias.aspx" or parsed.fragment:
        return ""
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if not query or any(key not in {"n1", "n2", "n3"} for key, _ in query):
        return ""
    if any(not re.fullmatch(r"[A-Za-z0-9-]*", value) for _, value in query):
        return ""
    return urllib.parse.urlunparse(("https", TARKETTNET_HOST, parsed.path, "", urllib.parse.urlencode(query), ""))


def _safe_product_url(value: str, code: str) -> str:
    url = urllib.parse.urljoin(TARKETTNET_ORIGIN, str(value or "").strip())
    parsed = urllib.parse.urlparse(url)
    expected_prefix = f"/vendas/{code.lower()}-"
    if parsed.scheme != "https" or parsed.hostname != TARKETTNET_HOST:
        return ""
    if not parsed.path.lower().startswith(expected_prefix) or not parsed.path.lower().endswith("/0.htm"):
        return ""
    return urllib.parse.urlunparse(("https", TARKETTNET_HOST, parsed.path, "", "", ""))


def _safe_image_url(value: str, code: str) -> str:
    url = urllib.parse.urljoin(TARKETTNET_ORIGIN, str(value or "").strip())
    parsed = urllib.parse.urlparse(url)
    filename = parsed.path.rsplit("/", 1)[-1].lower()
    if parsed.scheme != "https" or parsed.hostname != TARKETTNET_HOST:
        return ""
    if not parsed.path.startswith("/imagens/produtos/productos_tarkettnet/"):
        return ""
    if "sem_img" in filename or code.lower() not in filename:
        return ""
    return urllib.parse.urlunparse(("https", TARKETTNET_HOST, parsed.path, "", "", ""))


@dataclass(frozen=True)
class PortalProduct:
    code: str
    name: str
    unit: str
    unit_price: Decimal
    available_quantity: Decimal | None
    product_url: str
    image_url: str
    category_url: str

    def __post_init__(self) -> None:
        code = _clean_text(self.code)
        if not code or not re.fullmatch(r"[A-Za-z0-9._/-]+", code):
            raise ValueError("Codigo Tarkettnet invalido")
        name = _clean_text(self.name)
        unit = _clean_text(self.unit)
        price = _decimal(self.unit_price)
        stock = None if self.available_quantity is None else _decimal(self.available_quantity)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "unit", unit)
        object.__setattr__(self, "unit_price", price)
        object.__setattr__(self, "available_quantity", stock)
        object.__setattr__(self, "product_url", _safe_product_url(self.product_url, code))
        object.__setattr__(self, "image_url", _safe_image_url(self.image_url, code))
        object.__setattr__(self, "category_url", _safe_category_url(self.category_url))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "unit": self.unit,
            "unit_price": _json_number(self.unit_price),
            "available_quantity": None if self.available_quantity is None else _json_number(self.available_quantity),
            "product_url": self.product_url,
            "image_url": self.image_url,
            "category_url": self.category_url,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PortalProduct":
        return cls(
            code=raw.get("code", ""),
            name=raw.get("name", ""),
            unit=raw.get("unit", ""),
            unit_price=raw.get("unit_price", 0),
            available_quantity=raw.get("available_quantity"),
            product_url=raw.get("product_url", ""),
            image_url=raw.get("image_url", ""),
            category_url=raw.get("category_url", ""),
        )


class _CategoryParser(HTMLParser):
    def __init__(self, category_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.category_url = category_url
        self.card_depth = 0
        self.current: dict[str, Any] = {}
        self.current_p_class: str | None = None
        self.current_p: list[str] = []
        self.in_stock_table = False
        self.stock_table_depth = 0
        self.in_cell = False
        self.cell: list[str] = []
        self.row: list[str] = []
        self.stock_rows: list[list[str]] = []
        self.items: list[PortalProduct] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key.lower(): value or "" for key, value in attrs}
        classes = set(data.get("class", "").split())
        if not self.card_depth and tag == "div" and "card-produto" in classes:
            self.card_depth = 1
            self.current = {}
            self.stock_rows = []
            return
        if not self.card_depth:
            return
        if tag == "div":
            self.card_depth += 1
        if tag == "a" and not self.current.get("product_url"):
            href = data.get("href", "")
            if href.lower().endswith("/0.htm"):
                self.current["product_url"] = href
        elif tag == "img" and not self.current.get("image_url"):
            if data.get("id", "").endswith("_aImagem"):
                self.current["image_url"] = data.get("src", "")
        elif tag == "p":
            self.current_p_class = data.get("class", "")
            self.current_p = []
        elif tag == "table" and data.get("id", "").endswith("_GridEstoque"):
            self.in_stock_table = True
            self.stock_table_depth = 1
        elif self.in_stock_table and tag == "table":
            self.stock_table_depth += 1
        elif self.in_stock_table and tag == "tr":
            self.row = []
        elif self.in_stock_table and tag in {"td", "th"}:
            self.in_cell = True
            self.cell = []

    def handle_endtag(self, tag: str) -> None:
        if not self.card_depth:
            return
        if tag == "p" and self.current_p_class is not None:
            text = _clean_text("".join(self.current_p))
            if "titulo" in self.current_p_class.split():
                self.current["title"] = text
            elif "valor" in self.current_p_class.split():
                self.current["price"] = text
            elif text.startswith("(en ") and text.endswith(")"):
                self.current["unit"] = text[4:-1].strip()
            self.current_p_class = None
            self.current_p = []
        elif self.in_stock_table and tag in {"td", "th"} and self.in_cell:
            self.row.append(_clean_text("".join(self.cell)))
            self.in_cell = False
            self.cell = []
        elif self.in_stock_table and tag == "tr":
            if self.row:
                self.stock_rows.append(self.row)
            self.row = []
        elif self.in_stock_table and tag == "table":
            self.stock_table_depth -= 1
            if self.stock_table_depth <= 0:
                self.in_stock_table = False
        if tag == "div":
            self.card_depth -= 1
            if self.card_depth == 0:
                self._finish_card()

    def handle_data(self, data: str) -> None:
        if self.current_p_class is not None:
            self.current_p.append(data)
        if self.in_cell:
            self.cell.append(data)

    def _finish_card(self) -> None:
        title = _clean_text(self.current.get("title"))
        match = re.match(r"^(\S+)\s*-\s*(.+)$", title)
        if not match or not self.current.get("price"):
            return
        stock_values: list[Decimal] = []
        for row in self.stock_rows:
            if len(row) < 2:
                continue
            try:
                stock_values.append(_decimal(row[-1]))
            except ValueError:
                continue
        self.items.append(
            PortalProduct(
                code=match.group(1),
                name=match.group(2),
                unit=self.current.get("unit", ""),
                unit_price=_decimal(self.current["price"]),
                available_quantity=sum(stock_values, Decimal("0")) if stock_values else None,
                product_url=self.current.get("product_url", ""),
                image_url=self.current.get("image_url", ""),
                category_url=self.category_url,
            )
        )


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        url = _safe_category_url(href)
        if url:
            self.urls.add(url)


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden: dict[str, str] = {}
        self.fields: dict[str, str] = {}
        self.action = ""
        self._in_form = False
        self._select_name = ""
        self._select_first_value = ""
        self._select_selected_value: str | None = None
        self._option_value = ""
        self._option_text: list[str] = []
        self._option_selected = False
        self._textarea_name = ""
        self._textarea_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if tag == "form" and not self.action:
            self.action = str(data.get("action") or "")
            self._in_form = True
            return
        if not self._in_form:
            return
        if tag == "select" and data.get("name"):
            self._select_name = str(data["name"])
            self._select_first_value = ""
            self._select_selected_value = None
            return
        if tag == "option" and self._select_name:
            self._option_value = str(data.get("value") or "")
            self._option_text = []
            self._option_selected = "selected" in data
            return
        if tag == "textarea" and data.get("name"):
            self._textarea_name = str(data["name"])
            self._textarea_text = []
            return
        if tag != "input" or not data.get("name"):
            return
        input_type = str(data.get("type") or "text").lower()
        if input_type in {"submit", "button", "image", "file", "reset"}:
            return
        if input_type in {"checkbox", "radio"} and "checked" not in data:
            return
        name = str(data["name"])
        value = str(data.get("value") or "")
        self.fields[name] = value
        if input_type == "hidden":
            self.hidden[name] = value

    def handle_data(self, data: str) -> None:
        if self._option_text is not None and self._select_name:
            self._option_text.append(data)
        if self._textarea_name:
            self._textarea_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._in_form:
            self._in_form = False
            return
        if not self._in_form:
            return
        if tag == "option" and self._select_name:
            value = self._option_value or _clean_text("".join(self._option_text))
            if not self._select_first_value:
                self._select_first_value = value
            if self._option_selected:
                self._select_selected_value = value
            self._option_value = ""
            self._option_text = []
            self._option_selected = False
            return
        if tag == "select" and self._select_name:
            self.fields[self._select_name] = self._select_selected_value or self._select_first_value
            self._select_name = ""
            self._select_first_value = ""
            self._select_selected_value = None
            return
        if tag == "textarea" and self._textarea_name:
            self.fields[self._textarea_name] = "".join(self._textarea_text)
            self._textarea_name = ""
            self._textarea_text = []


class _PaginationParser(HTMLParser):
    _POSTBACK_RE = re.compile(r"__doPostBack\('([^']+)'\s*,\s*'[^']*'\)", re.IGNORECASE)

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.current_page = 1
        self._anchor_target = ""
        self._anchor_text: list[str] = []
        self.actions: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if tag == "input" and str(data.get("name") or "").endswith("$PageNumber"):
            try:
                self.current_page = max(1, int(str(data.get("value") or "1")))
            except ValueError:
                self.current_page = 1
            return
        if tag != "a":
            return
        match = self._POSTBACK_RE.search(str(data.get("href") or ""))
        target = match.group(1) if match else ""
        if "rptPaging" not in target or not target.endswith("$btnPage"):
            return
        self._anchor_target = target
        self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._anchor_target:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._anchor_target:
            return
        label = _clean_text("".join(self._anchor_text))
        self.actions.append((label, self._anchor_target))
        self._anchor_target = ""
        self._anchor_text = []


def parse_tarkettnet_category(html: str, category_url: str) -> list[PortalProduct]:
    safe_url = _safe_category_url(category_url)
    if not safe_url:
        raise ValueError("URL de categoria Tarkettnet invalida")
    parser = _CategoryParser(safe_url)
    parser.feed(str(html or ""))
    return parser.items


def parse_tarkettnet_category_urls(html: str) -> list[str]:
    parser = _LinkParser()
    parser.feed(str(html or ""))
    return sorted(parser.urls)


def _parse_pagination(html: str) -> tuple[int, list[tuple[str, str]]]:
    parser = _PaginationParser()
    parser.feed(str(html or ""))
    return parser.current_page, parser.actions


def _parse_result_count(html: str) -> int | None:
    match = re.search(r"Encontrado\(s\):\s*([0-9.,]+)", str(html or ""), re.IGNORECASE)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None


def _build_postback(
    html: str,
    *,
    event_target: str,
    extra_fields: dict[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    if not re.fullmatch(r"ctl00\$wrapper_content\$[A-Za-z0-9_$]+", event_target):
        raise RuntimeError("Postback Tarkettnet no permitido")
    parser = _FormParser()
    parser.feed(str(html or ""))
    action = _safe_category_form_action(parser.action)
    if not action:
        raise RuntimeError("Formulario de categoria Tarkettnet invalido")
    payload = dict(parser.fields)
    payload.update(
        {
            "ctl00$tsm": f"ctl00$wrapper_content$UpdatePanel1|{event_target}",
            "__EVENTTARGET": event_target,
            "__EVENTARGUMENT": "",
            "__ASYNCPOST": "true",
            "ctl00$wrapper_content$ddOrdenacao": "qs",
            "ctl00$wrapper_content$ddQtdPagina": str(PAGE_SIZE),
        }
    )
    if extra_fields:
        payload.update(extra_fields)
    return action, payload


class TarkettnetClient:
    def __init__(self, *, timeout: int = 45) -> None:
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def _request(
        self,
        url: str,
        *,
        data: dict[str, str] | None = None,
        referer: str | None = None,
    ) -> tuple[str, str]:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != TARKETTNET_HOST:
            raise RuntimeError("Host Tarkettnet no permitido")
        body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
        request = urllib.request.Request(
            url,
            data=body,
            method="POST" if body is not None else "GET",
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                **(
                    {
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "X-MicrosoftAjax": "Delta=true",
                        "X-Requested-With": "XMLHttpRequest",
                        **({"Referer": referer} if referer else {}),
                    }
                    if data and data.get("__ASYNCPOST") == "true"
                    else {}
                ),
            },
        )
        with self._opener.open(request, timeout=self.timeout) as response:
            final_url = response.geturl()
            final = urllib.parse.urlparse(final_url)
            if final.scheme != "https" or final.hostname != TARKETTNET_HOST:
                raise RuntimeError("Redirect Tarkettnet fuera del host permitido")
            content = response.read(MAX_RESPONSE_BYTES + 1)
            if len(content) > MAX_RESPONSE_BYTES:
                raise RuntimeError("Respuesta Tarkettnet demasiado grande")
            charset = response.headers.get_content_charset() or "utf-8"
            return content.decode(charset, errors="replace"), final_url

    def login(self, email: str, password: str) -> None:
        if not _clean_text(email) or not str(password or ""):
            raise RuntimeError("Credenciales Tarkettnet no configuradas")
        html, _ = self._request(TARKETTNET_LOGIN_URL)
        parser = _FormParser()
        parser.feed(html)
        payload = {
            **parser.hidden,
            "__EVENTTARGET": "lkbEnviar",
            "__EVENTARGUMENT": "",
            "tbUsuario": str(email).strip(),
            "tbSenha": str(password),
        }
        home_html, final_url = self._request(TARKETTNET_LOGIN_URL, data=payload)
        if "/login" in final_url.lower() or "Mi Carrito" not in home_html:
            raise RuntimeError("No fue posible autenticar Tarkettnet")

    def fetch_products(self) -> list[PortalProduct]:
        home_html, _ = self._request(TARKETTNET_HOME_URL)
        category_urls = set(DEFAULT_CATEGORY_URLS)
        category_urls.update(parse_tarkettnet_category_urls(home_html))
        products: dict[str, PortalProduct] = {}
        for category_url in sorted(category_urls):
            for html in self._fetch_category_pages(category_url):
                for product in parse_tarkettnet_category(html, category_url):
                    current = products.get(product.code)
                    if current is None or _product_quality(product) > _product_quality(current):
                        products[product.code] = product
        if not products:
            raise RuntimeError("Tarkettnet no devolvio productos")
        return sorted(products.values(), key=lambda item: item.code)

    def _fetch_category_pages(self, category_url: str) -> list[str]:
        html, final_url = self._request(category_url)
        if "/login" in final_url.lower():
            raise RuntimeError("Sesion Tarkettnet expirada")

        expected_total = _parse_result_count(html)
        if expected_total is not None and expected_total > PAGE_SIZE:
            raise RuntimeError("Tarkettnet excedio el maximo verificable de productos por categoria")
        if expected_total is not None and expected_total <= 30:
            return [html]

        if 'name="ctl00$wrapper_content$ddQtdPagina"' in html:
            action, payload = _build_postback(
                html,
                event_target="ctl00$wrapper_content$ddQtdPagina",
            )
            html, final_url = self._request(action, data=payload, referer=category_url)
            if "/login" in final_url.lower():
                raise RuntimeError("Sesion Tarkettnet expirada")
        parsed_count = len(parse_tarkettnet_category(html, category_url))
        if expected_total is not None and parsed_count != expected_total:
            raise RuntimeError("La categoria Tarkettnet no se sincronizo completa")
        return [html]


def _product_quality(product: PortalProduct) -> tuple[int, int, int, int]:
    return (
        int(bool(product.image_url)),
        int(product.available_quantity is not None),
        int(bool(product.product_url)),
        len(product.name),
    )


def merge_tarkettnet_catalog(
    base_catalog: dict[str, Any],
    records: Iterable[PortalProduct],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    raw_items = base_catalog.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Catalogo base Tarkett vacio")
    by_code = {record.code: record for record in records}
    items: list[dict[str, Any]] = []
    matches = 0
    for raw in raw_items:
        item = dict(raw)
        item.setdefault("unit_price", 0)
        item.setdefault("price_source", "missing")
        item.setdefault("stock_source", "inventory_file")
        record = by_code.get(_clean_text(item.get("code")))
        if record is not None:
            matches += 1
            if record.name:
                item["name"] = record.name
            if record.unit:
                item["unit"] = record.unit
            item["unit_price"] = _json_number(record.unit_price)
            item["price_source"] = "tarkettnet_code_match"
            if record.available_quantity is not None:
                item["available_quantity"] = _json_number(record.available_quantity)
                item["stock_source"] = "tarkettnet_code_match"
            if record.product_url:
                item["product_url"] = record.product_url
            if record.image_url:
                item["image_url"] = record.image_url
            item["match_status"] = "tarkettnet_code_match"
        items.append(item)

    hash_payload = {
        "source_file": str(base_catalog.get("source_file") or ""),
        "items": items,
    }
    source_hash = hashlib.sha256(
        json.dumps(hash_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if source_hash == str(base_catalog.get("source_hash") or ""):
        effective_generated_at = str(base_catalog.get("generated_at") or "")
    else:
        effective_generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    return {
        "source_file": str(base_catalog.get("source_file") or ""),
        "source_hash": source_hash,
        "generated_at": effective_generated_at,
        "total": len(items),
        "tarkettnet_matches": matches,
        "tarkettnet_price_matches": sum(item.get("price_source") == "tarkettnet_code_match" for item in items),
        "tarkettnet_image_matches": sum(
            item.get("match_status") == "tarkettnet_code_match"
            and TARKETTNET_HOST in str(item.get("image_url") or "")
            for item in items
        ),
        "items": items,
    }


def sync_catalog_from_tarkettnet(
    base_catalog: dict[str, Any],
    *,
    email: str,
    password: str,
    client: TarkettnetClient | None = None,
) -> dict[str, Any]:
    portal = client or TarkettnetClient()
    portal.login(email, password)
    return merge_tarkettnet_catalog(base_catalog, portal.fetch_products())
