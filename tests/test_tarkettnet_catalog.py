from decimal import Decimal

from mobiliti_saas.quote_engine.tarkettnet_catalog import (
    PortalProduct,
    _build_postback,
    _parse_pagination,
    merge_tarkettnet_catalog,
    parse_tarkettnet_category,
)


CATEGORY_HTML = """
<html><body>
  <div class="prod card-produto">
    <a href="https://www.tarkettnet.com.mx/vendas/24174113-piso-ambienta-stone-light-porcelain-600x600mm/0.htm">
      <img id="ctl00_wrapper_content_rptVitrineItens_ctl01_aImagem"
           src="https://www.tarkettnet.com.mx/imagens/produtos/productos_tarkettnet/24174113_normal.jpg">
    </a>
    <div class="precos">
      <p class="titulo">24174113 - Piso Ambienta Stone Light Porcelain 600x600mm</p>
      <p class="valor">$172.00</p>
      <p>(en MTK - metro cuadrado)</p>
      <table id="ctl00_wrapper_content_rptVitrineItens_ctl01_GridEstoque">
        <tr><th>Lote</th><th>Cant</th></tr>
        <tr><td>0</td><td>3.60</td></tr>
        <tr><td>1</td><td>2.40</td></tr>
      </table>
    </div>
  </div>
  <div class="prod card-produto">
    <a href="https://www.tarkettnet.com.mx/vendas/710550010-desso-grain-b867-9501-b1-50x50/0.htm">
      <img id="ctl00_wrapper_content_rptVitrineItens_ctl02_aImagem"
           src="https://www.tarkettnet.com.mx/imagens/produtos/productos_tarkettnet/sem_img.jpg">
    </a>
    <p class="titulo">710550010 - Desso Grain B867 9501 B1 50x50</p>
    <p class="valor">$523.70</p>
    <p>(en MTK - metro cuadrado)</p>
  </div>
</body></html>
"""


def test_parse_tarkettnet_category_extracts_exact_code_price_image_unit_and_stock():
    items = parse_tarkettnet_category(
        CATEGORY_HTML,
        "https://www.tarkettnet.com.mx/vendas/lvt-comercial/ambienta.html",
    )

    assert len(items) == 2
    first = items[0]
    assert first.code == "24174113"
    assert first.name == "Piso Ambienta Stone Light Porcelain 600x600mm"
    assert first.unit == "MTK - metro cuadrado"
    assert first.unit_price == Decimal("172.00")
    assert first.available_quantity == Decimal("6.00")
    assert first.image_url.endswith("/24174113_normal.jpg")
    assert first.product_url.endswith("/24174113-piso-ambienta-stone-light-porcelain-600x600mm/0.htm")
    assert items[1].image_url == ""


def test_portal_product_rejects_image_or_link_for_a_different_code():
    product = PortalProduct(
        code="24174113",
        name="Light Porcelain",
        unit="MTK - metro cuadrado",
        unit_price=Decimal("172"),
        available_quantity=Decimal("3.6"),
        product_url="https://www.tarkettnet.com.mx/vendas/24174124-grafito/0.htm",
        image_url="https://www.tarkettnet.com.mx/imagens/produtos/productos_tarkettnet/24174124_normal.jpg",
        category_url="https://www.tarkettnet.com.mx/vendas/lvt-comercial/ambienta.html",
    )

    assert product.product_url == ""
    assert product.image_url == ""


def test_category_postback_requests_sixty_and_parses_next_page_without_async_state():
    html = """
    <form action="https://www.tarkettnet.com.mx/cli_rep/categorias.aspx?n1=lvt-comercial&n2=&n3=ambienta">
      <input type="hidden" name="__VIEWSTATE" value="state">
      <input type="hidden" name="ctl00$tsm" value="async-state">
      <input type="hidden" name="ctl00$wrapper_content$PageNumber" value="1">
      <select name="ctl00$wrapper_content$ddQtdPagina"><option selected>30</option></select>
      <a href="javascript:__doPostBack('ctl00$wrapper_content$rptPagingSup$ctl03$btnPage','')">2</a>
      <a href="javascript:__doPostBack('ctl00$wrapper_content$rptPagingSup$ctl04$btnPage','')">»</a>
    </form>
    """

    page, actions = _parse_pagination(html)
    action, payload = _build_postback(
        html,
        event_target="ctl00$wrapper_content$ddQtdPagina",
    )

    assert page == 1
    assert actions[0] == ("2", "ctl00$wrapper_content$rptPagingSup$ctl03$btnPage")
    assert action.endswith("/cli_rep/categorias.aspx?n1=lvt-comercial&n2=&n3=ambienta")
    assert payload["ctl00$wrapper_content$ddQtdPagina"] == "60"
    assert payload["__EVENTTARGET"] == "ctl00$wrapper_content$ddQtdPagina"
    assert payload["ctl00$tsm"].endswith("|ctl00$wrapper_content$ddQtdPagina")
    assert payload["__ASYNCPOST"] == "true"


def test_category_postback_rejects_untrusted_form_action():
    html = '<form action="https://example.com/cli_rep/categorias.aspx?n1=x"></form>'

    try:
        _build_postback(html, event_target="ctl00$wrapper_content$ddQtdPagina")
    except RuntimeError as exc:
        assert "invalido" in str(exc)
    else:
        raise AssertionError("Un formulario externo no debe aceptarse")


def test_category_discovery_ignores_top_level_aggregate_pages():
    from mobiliti_saas.quote_engine.tarkettnet_catalog import parse_tarkettnet_category_urls

    urls = parse_tarkettnet_category_urls(
        '<a href="/vendas/lvt-comercial.html">LVT</a>'
        '<a href="/vendas/lvt-comercial/ambienta.html">Ambienta</a>'
    )

    assert urls == ["https://www.tarkettnet.com.mx/vendas/lvt-comercial/ambienta.html"]


def test_merge_tarkettnet_catalog_overrides_exact_code_and_preserves_fallback_image():
    base = {
        "source_file": "Inventario Tarkett.xls",
        "source_hash": "inventory-hash",
        "generated_at": "2026-07-08T00:00:00+00:00",
        "total": 2,
        "items": [
            {
                "code": "24174113",
                "name": "Nombre viejo",
                "unit": "MTK - metro cuadrado",
                "available_quantity": 16.9,
                "unit_price": 0,
                "price_source": "missing",
                "stock_source": "inventory_file",
                "product_url": "https://tarkett.com.mx/producto/light-porcelain/",
                "image_url": "https://tarkett.com.mx/light-old.jpg",
                "match_status": "name_match",
            },
            {
                "code": "710550010",
                "name": "Desso Grain B867 9501 B1 50x50",
                "unit": "MTK - metro cuadrado",
                "available_quantity": 4,
                "unit_price": 0,
                "price_source": "missing",
                "stock_source": "inventory_file",
                "product_url": "https://tarkett.com.mx/producto/grain/",
                "image_url": "https://tarkett.com.mx/grain-fallback.jpg",
                "match_status": "name_match",
            },
        ],
    }
    records = [
        PortalProduct(
            code="24174113",
            name="Piso Ambienta Stone Light Porcelain 600x600mm",
            unit="MTK - metro cuadrado",
            unit_price=Decimal("172"),
            available_quantity=Decimal("6"),
            product_url="https://www.tarkettnet.com.mx/vendas/24174113-light/0.htm",
            image_url="https://www.tarkettnet.com.mx/imagens/produtos/productos_tarkettnet/24174113_normal.jpg",
            category_url="https://www.tarkettnet.com.mx/vendas/lvt-comercial/ambienta.html",
        ),
        PortalProduct(
            code="710550010",
            name="Desso Grain B867 9501 B1 50x50",
            unit="MTK - metro cuadrado",
            unit_price=Decimal("523.70"),
            available_quantity=None,
            product_url="https://www.tarkettnet.com.mx/vendas/710550010-grain/0.htm",
            image_url="",
            category_url="https://www.tarkettnet.com.mx/vendas/alfombra/grain.html",
        ),
    ]

    merged = merge_tarkettnet_catalog(base, records, generated_at="2026-07-14T12:00:00+00:00")
    by_code = {item["code"]: item for item in merged["items"]}

    assert by_code["24174113"]["name"] == "Piso Ambienta Stone Light Porcelain 600x600mm"
    assert by_code["24174113"]["unit_price"] == 172
    assert by_code["24174113"]["available_quantity"] == 6
    assert by_code["24174113"]["price_source"] == "tarkettnet_code_match"
    assert by_code["24174113"]["stock_source"] == "tarkettnet_code_match"
    assert by_code["24174113"]["match_status"] == "tarkettnet_code_match"
    assert "24174113_normal.jpg" in by_code["24174113"]["image_url"]
    assert by_code["710550010"]["unit_price"] == 523.7
    assert by_code["710550010"]["available_quantity"] == 4
    assert by_code["710550010"]["stock_source"] == "inventory_file"
    assert by_code["710550010"]["image_url"] == "https://tarkett.com.mx/grain-fallback.jpg"
    assert merged["source_hash"] != "inventory-hash"
    assert merged["tarkettnet_matches"] == 2
