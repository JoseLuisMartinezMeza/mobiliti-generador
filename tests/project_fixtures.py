def valid_project_payload():
    return {
        "schema_version": 1,
        "quote_fields": {
            "proyecto": "Oficinas",
            "cliente": "Cliente",
            "correo": "cliente@example.com",
            "telefono": "33",
            "direccion": "GDL",
            "razon_social": "Cliente SA",
            "quote_currency": "MXN",
            "descuento": "40",
        },
        "sections": [{"section_id": "section-1", "concept": "Recepción", "position": 0}],
        "lines": [
            {
                "line_id": "11111111-1111-4111-8111-111111111111",
                "role": "principal",
                "section_id": "section-1",
                "parent_line_id": None,
                "position": 0,
                "quantity": "10",
                "source": "catalog",
                "catalog": "sunon",
                "official_code": "CHAIR-1",
                "identity": {
                    "internal_id": "sunon:chair-1",
                    "base_option_id": "",
                    "add_on_option_ids": [],
                },
                "display_cache": {"name": "Silla", "code": "CHAIR-1", "image_url": ""},
            },
            {
                "line_id": "22222222-2222-4222-8222-222222222222",
                "role": "complement",
                "section_id": None,
                "parent_line_id": "11111111-1111-4111-8111-111111111111",
                "position": 0,
                "quantity": "1",
                "quantity_mode": "per_parent_unit",
                "source": "imported",
                "import_id": "33333333-3333-4333-8333-333333333333",
                "source_row": 14,
                "source_currency": "USD",
                "official_code": "HEAD-1",
                "provider": "Sunon",
                "name": "Cabecera",
                "description": "Cabecera",
                "dimension": "",
                "unit_price": "20.00",
                "image_asset_key": "",
                "source_asset_key": (
                    "projects/7/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/"
                    "sources/source.xlsx"
                ),
                "display_cache": {"name": "Cabecera", "code": "HEAD-1", "image_url": ""},
            },
        ],
    }
