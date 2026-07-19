import json
import subprocess
from pathlib import Path

import pytest

from mobiliti_saas.quote_engine.mixed_catalog import (
    mixed_cart_key as python_mixed_cart_key,
    preflight_mixed_catalog_items,
)


MODULE_PATH = Path("mobiliti_saas/web/src/mixedCart.js")
EXPORTS = (
    "MIXED_CATALOGS",
    "mixedCartKey",
    "createMixedCartLine",
    "validateLineQuantity",
    "lineNeedsAvailabilityConfirmation",
    "lineNeedsPriceConfirmation",
    "upsertMixedCartLine",
    "updateMixedCartQuantity",
    "removeMixedCartLine",
    "toMixedQuoteItem",
)


def run_mixed_cart_js(source):
    module_url = MODULE_PATH.resolve().as_uri()
    script = (
        f'import {{ {", ".join(EXPORTS)} }} from {json.dumps(module_url)};\n'
        f"{source}"
    )
    completed = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def supplier_line_source(
    *,
    catalog="sonara",
    internal_id="sonara:panel",
    quantity="1",
    rules=None,
    warnings=None,
):
    rules = rules or {
        "min": "1",
        "step": "1",
        "maxDecimals": 0,
        "max": "1000000",
        "integer": True,
    }
    warnings = warnings or []
    return json.dumps(
        {
            "catalog": catalog,
            "identity": {
                "internal_id": internal_id,
                "base_option_id": "",
                "add_on_option_ids": [],
            },
            "quantity": quantity,
            "quantityRules": rules,
            "snapshot": {
                "name": "Panel",
                "code": "Codigo por verificar",
                "image_url": "",
                "unit": "PZA",
                "availability": "",
                "configuration": "",
                "warnings": warnings,
            },
        }
    )


def test_mixed_cart_keys_are_stable_and_configuration_sensitive():
    result = run_mixed_cart_js(
        """
      const keys = [
        mixedCartKey("tarkett", {code: "25731726"}),
        mixedCartKey("offiho", {inventory_key: "OHE-405 NEGRO ALUFSEN"}),
        mixedCartKey("alma", {
          internal_id: "alma:desk-1", base_option_id: "base-a",
          add_on_option_ids: ["addon-b", "addon-a"]
        }),
        mixedCartKey("sunon", {
          internal_id: "mesa|uno:日本", base_option_id: "base|:azul",
          add_on_option_ids: ["cojín:b", "cojín|a"]
        })
      ];
      console.log(JSON.stringify(keys));
    """
    )
    assert result == [
        "tarkett:25731726",
        "offiho:OHE-405 NEGRO ALUFSEN",
        'alma:["alma:desk-1","base-a",["addon-a","addon-b"]]',
        'sunon:["mesa|uno:日本","base|:azul",["cojín:b","cojín|a"]]',
    ]


def test_mixed_cart_keys_reject_ambiguous_or_unsupported_identities():
    result = run_mixed_cart_js(
        """
      const cases = [
        () => mixedCartKey("unknown", {code: "X"}),
        () => mixedCartKey("tarkett", {code: "  "}),
        () => mixedCartKey("offiho", {}),
        () => mixedCartKey("alma", {internal_id: "x", base_option_id: "", add_on_option_ids: ["a", "a"]}),
        () => mixedCartKey("alma", {internal_id: "x", base_option_id: "", add_on_option_ids: "a"})
      ];
      console.log(JSON.stringify(cases.map(run => {
        try { run(); return "accepted"; } catch (error) { return error.message; }
      })));
    """
    )
    assert result == [
        "Catalogo mixto no soportado",
        "code requerido",
        "inventory_key requerido",
        "Add-ons duplicados",
        "Add-ons invalidos",
    ]


def test_upsert_accumulates_without_float_drift_and_preserves_other_catalogs():
    result = run_mixed_cart_js(
        """
      const tarkett = createMixedCartLine({
        catalog: "tarkett", identity: {code: "T-1"}, quantity: "0.1",
        quantityRules: {min: "0.000001", step: "0.000001", maxDecimals: 6, max: "5"},
        snapshot: {name: "Tarkett", code: "T-1", image_url: "", unit: "M2", availability: "", configuration: "", warnings: []}
      });
      const sonara = createMixedCartLine({
        catalog: "sonara", identity: {internal_id: "sonara:panel", base_option_id: "", add_on_option_ids: []},
        quantity: "1", quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {name: "Panel", code: "Codigo por verificar", image_url: "", unit: "PZA", availability: "", configuration: "", warnings: ["Codigo por verificar"]}
      });
      let lines = upsertMixedCartLine([], tarkett);
      lines = upsertMixedCartLine(lines, sonara);
      lines = upsertMixedCartLine(lines, {...tarkett, quantity: "0.2"});
      console.log(JSON.stringify(lines.map(line => [line.key, line.quantity])));
    """
    )
    assert result == [
        ["tarkett:T-1", "0.3"],
        ['sonara:["sonara:panel","",[]]', "1"],
    ]


def test_quantity_precision_is_enforced_per_catalog_without_float_rounding():
    result = run_mixed_cart_js(
        """
      const offiho = createMixedCartLine({
        catalog: "offiho", identity: {inventory_key: "OFF-1"}, quantity: "1.001",
        quantityRules: {min: "0.001", step: "0.001", maxDecimals: 3, max: "1000000"},
        snapshot: {name: "Offiho", code: "OFF-1", image_url: "", unit: "PZA", availability: "", configuration: "Negro", warnings: []}
      });
      let message = "";
      try { updateMixedCartQuantity([offiho], offiho.key, "1.0001"); }
      catch (error) { message = error.message; }
      console.log(JSON.stringify({allowed: offiho.quantity, rejected: message}));
    """
    )
    assert result == {
        "allowed": "1.001",
        "rejected": "Cantidad excede 3 decimales",
    }


def test_visual_supplier_configurations_are_distinct_and_display_named():
    result = run_mixed_cart_js(
        """
      const makeLine = (addOnId, configuration) => createMixedCartLine({
        catalog: "alma",
        identity: {internal_id: "alma:desk", base_option_id: "base-a", add_on_option_ids: [addOnId]},
        quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {name: "Escritorio ALMA", code: "AL-1", image_url: "", unit: "PZA", availability: "Sobre pedido", configuration, warnings: []}
      });
      const lines = [
        makeLine("addon-a", "Base A + Electrificacion A"),
        makeLine("addon-b", "Base A + Pasacables B")
      ];
      console.log(JSON.stringify(lines.map(line => ({
        key: line.key, name: line.snapshot.name,
        configuration: line.snapshot.configuration, serialized: toMixedQuoteItem(line)
      }))));
    """
    )
    assert result[0]["key"] != result[1]["key"]
    assert [row["name"] for row in result] == ["Escritorio ALMA", "Escritorio ALMA"]
    assert [row["configuration"] for row in result] == [
        "Base A + Electrificacion A",
        "Base A + Pasacables B",
    ]
    assert all("configuration" not in row["serialized"] for row in result)


def test_line_shape_is_exact_and_defensively_copies_visual_and_identity_data():
    result = run_mixed_cart_js(
        """
      const identity = {
        internal_id: "alma:desk", base_option_id: "base-a",
        add_on_option_ids: ["b", "a"], ignored: "commercial leak"
      };
      const rules = {min: "1", step: "1", maxDecimals: 0, max: "10", integer: true};
      const warnings = ["visual"];
      const snapshot = {
        name: "Desk", code: "AL-1", image_url: "https://example.test/a.png",
        unit: "PZA", availability: "5", configuration: "x".repeat(2001), warnings,
        unit_price: "1", exchange_rate: "999", product_url: "https://evil.test"
      };
      const line = createMixedCartLine({catalog: "alma", identity, quantity: "2.000000", quantityRules: rules, snapshot});
      identity.add_on_option_ids.push("c"); warnings.push("changed"); rules.max = "2";
      console.log(JSON.stringify({
        lineKeys: Object.keys(line).sort(),
        identity: line.identity,
        originalIdentity: identity.add_on_option_ids,
        quantity: line.quantity,
        rulesMax: line.quantityRules.max,
        snapshotKeys: Object.keys(line.snapshot).sort(),
        warnings: line.snapshot.warnings,
        configurationLength: line.snapshot.configuration.length
      }));
    """
    )
    assert result == {
        "lineKeys": ["catalog", "identity", "key", "quantity", "quantityRules", "snapshot"],
        "identity": {
            "internal_id": "alma:desk",
            "base_option_id": "base-a",
            "add_on_option_ids": ["a", "b"],
        },
        "originalIdentity": ["b", "a", "c"],
        "quantity": "2",
        "rulesMax": "10",
        "snapshotKeys": [
            "availability",
            "code",
            "configuration",
            "image_url",
            "name",
            "unit",
            "warnings",
        ],
        "warnings": ["visual"],
        "configurationLength": 2000,
    }


@pytest.mark.parametrize(
    ("quantity", "message"),
    [
        ("0.5", "Cantidad menor al minimo"),
        ("1.5", "Cantidad entera requerida"),
        ("2", "Incremento de cantidad invalido"),
        ("11", "Cantidad mayor al maximo permitido"),
        ("1.0000001", "Cantidad invalida"),
    ],
)
def test_quantity_limits_have_stable_errors(quantity, message):
    rules = {
        "min": "1",
        "step": "2",
        "maxDecimals": 6,
        "max": "10",
        "integer": quantity == "1.5",
    }
    source = supplier_line_source(quantity="1", rules=rules)
    result = run_mixed_cart_js(
        f"""
      const line = createMixedCartLine({source});
      let message = "accepted";
      try {{ validateLineQuantity(line, {json.dumps(quantity)}); }}
      catch (error) {{ message = error.message; }}
      console.log(JSON.stringify(message));
    """
    )
    assert result == message


def test_quantity_requires_decimal_strings_and_valid_precision_rules():
    source = supplier_line_source()
    result = run_mixed_cart_js(
        f"""
      const line = createMixedCartLine({source});
      const attempts = [
        () => validateLineQuantity(line, 2),
        () => validateLineQuantity({{...line, quantityRules: {{...line.quantityRules, maxDecimals: 7}}}}, "2")
      ];
      console.log(JSON.stringify(attempts.map(run => {{
        try {{ run(); return "accepted"; }} catch (error) {{ return error.message; }}
      }})));
    """
    )
    assert result == ["Cantidad invalida", "Precision de cantidad invalida"]


def test_commercial_max_is_required_positive_and_enforced_for_all_sources():
    offiho = {
        "catalog": "offiho",
        "identity": {"inventory_key": "OFF-1"},
        "quantity": "1",
        "quantityRules": {
            "min": "0.001",
            "step": "0.001",
            "maxDecimals": 3,
            "max": "1000000",
        },
        "snapshot": {
            "name": "Offiho",
            "code": "OFF-1",
            "image_url": "",
            "unit": "PZA",
            "availability": "",
            "configuration": "",
            "warnings": [],
        },
    }
    supplier = json.loads(supplier_line_source())
    result = run_mixed_cart_js(
        f"""
      const offihoInput = {json.dumps(offiho)};
      const supplierInput = {json.dumps(supplier)};
      const attempts = [
        () => createMixedCartLine({{...supplierInput, quantityRules: {{...supplierInput.quantityRules, max: undefined}}}}),
        () => createMixedCartLine({{...supplierInput, quantityRules: {{...supplierInput.quantityRules, max: "0"}}}}),
        () => createMixedCartLine({{...offihoInput, quantity: "1000000.001"}}),
        () => createMixedCartLine({{...supplierInput, quantity: "1000001"}})
      ];
      console.log(JSON.stringify(attempts.map(run => {{
        try {{ run(); return "accepted"; }} catch (error) {{ return error.message; }}
      }})));
    """
    )
    assert result == [
        "Maximo comercial requerido",
        "Maximo comercial invalido",
        "Cantidad mayor al maximo permitido",
        "Cantidad mayor al maximo permitido",
    ]


def test_confirmation_helpers_are_rule_driven_not_warning_text_driven():
    offiho = {
        "catalog": "offiho",
        "identity": {"inventory_key": "OFF-1"},
        "quantity": "7",
        "quantityRules": {
            "min": "0.001",
            "step": "0.001",
            "maxDecimals": 3,
            "max": "1000000",
            "warningAt": "5",
            "confirmOnInsufficient": True,
            "confirmOnMissingPrice": True,
        },
        "snapshot": {
            "name": "Offiho",
            "code": "OFF-1",
            "image_url": "",
            "unit": "PZA",
            "availability": "5",
            "configuration": "",
            "warnings": ["visual only"],
        },
    }
    warning_only = json.loads(supplier_line_source(warnings=["Precio por confirmar"]))
    out_of_stock = json.loads(supplier_line_source())
    out_of_stock["quantityRules"].update(
        {"warningAt": "0", "confirmOnInsufficient": True}
    )
    result = run_mixed_cart_js(
        f"""
      const offiho = createMixedCartLine({json.dumps(offiho)});
      const warningOnly = createMixedCartLine({json.dumps(warning_only)});
      const outOfStock = createMixedCartLine({json.dumps(out_of_stock)});
      console.log(JSON.stringify({{
        overstockAllowed: offiho.quantity,
        availability: [
          lineNeedsAvailabilityConfirmation(offiho),
          lineNeedsAvailabilityConfirmation(warningOnly),
          lineNeedsAvailabilityConfirmation(outOfStock)
        ],
        price: [lineNeedsPriceConfirmation(offiho), lineNeedsPriceConfirmation(warningOnly)]
      }}));
    """
    )
    assert result == {
        "overstockAllowed": "7",
        "availability": [True, False, True],
        "price": [True, False],
    }


def test_refresh_upsert_uses_incoming_rules_and_snapshot_without_mutating_inputs():
    result = run_mixed_cart_js(
        """
      const make = (quantity, max, image, warning) => createMixedCartLine({
        catalog: "offiho", identity: {inventory_key: "OFF-1"}, quantity,
        quantityRules: {
          min: "0.001", step: "0.001", maxDecimals: 3, max,
          warningAt: "1", confirmOnInsufficient: true,
          confirmOnMissingPrice: warning === "new"
        },
        snapshot: {name: "Offiho", code: "OFF-1", image_url: image, unit: "PZA", availability: "1", configuration: "", warnings: [warning]}
      });
      const oldLine = make("1", "100", "old.png", "old");
      const incoming = make("1", "3", "new.png", "new");
      const original = [oldLine];
      const refreshed = upsertMixedCartLine(original, incoming);
      let loweredLimit = "accepted";
      try { upsertMixedCartLine([make("2", "100", "old.png", "old")], make("2", "3", "new.png", "new")); }
      catch (error) { loweredLimit = error.message; }
      incoming.snapshot.warnings.push("caller mutation");
      console.log(JSON.stringify({
        quantity: refreshed[0].quantity,
        max: refreshed[0].quantityRules.max,
        image: refreshed[0].snapshot.image_url,
        warnings: refreshed[0].snapshot.warnings,
        needsAvailability: lineNeedsAvailabilityConfirmation(refreshed[0]),
        needsPrice: lineNeedsPriceConfirmation(refreshed[0]),
        oldUnchanged: original[0].snapshot.image_url,
        loweredLimit
      }));
    """
    )
    assert result == {
        "quantity": "2",
        "max": "3",
        "image": "new.png",
        "warnings": ["new"],
        "needsAvailability": True,
        "needsPrice": True,
        "oldUnchanged": "old.png",
        "loweredLimit": "Cantidad mayor al maximo permitido",
    }


def test_update_and_remove_are_immutable_and_preserve_unrelated_catalog_lines():
    result = run_mixed_cart_js(
        f"""
      const first = createMixedCartLine({supplier_line_source(internal_id="sonara:first")});
      const second = createMixedCartLine({supplier_line_source(catalog="lumbro", internal_id="lumbro:second")});
      const original = [first, second];
      const updated = updateMixedCartQuantity(original, first.key, "2");
      const removed = removeMixedCartLine(updated, first.key);
      let missing = "accepted";
      try {{ updateMixedCartQuantity(original, "missing", "2"); }}
      catch (error) {{ missing = error.message; }}
      console.log(JSON.stringify({{
        original: original.map(line => [line.key, line.quantity]),
        updated: updated.map(line => [line.key, line.quantity]),
        removed: removed.map(line => [line.key, line.quantity]),
        secondPreservedByReference: original[1] === updated[1],
        missing
      }}));
    """
    )
    assert result == {
        "original": [
            ['sonara:["sonara:first","",[]]', "1"],
            ['lumbro:["lumbro:second","",[]]', "1"],
        ],
        "updated": [
            ['sonara:["sonara:first","",[]]', "2"],
            ['lumbro:["lumbro:second","",[]]', "1"],
        ],
        "removed": [['lumbro:["lumbro:second","",[]]', "1"]],
        "secondPreservedByReference": True,
        "missing": "Linea de carrito no encontrada",
    }


def test_mixed_quote_serializer_sends_only_identity_configuration_and_quantity():
    result = run_mixed_cart_js(
        """
      const line = createMixedCartLine({
        catalog: "alma",
        identity: {internal_id: "alma:desk", base_option_id: "base-a", add_on_option_ids: ["b", "a"]},
        quantity: "2", quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {
          name: "Desk", code: "AL-1", image_url: "https://evil.test/x.png", unit: "PZA",
          availability: "5", configuration: "Base A + Electrificacion A",
          warnings: ["visual"], unit_price: "1", base_currency: "XXX",
          exchange_rate: "999", stock: "999", product_url: "https://evil.test"
        }
      });
      console.log(JSON.stringify({
        item: toMixedQuoteItem(line),
        snapshotKeys: Object.keys(line.snapshot).sort()
      }));
    """
    )
    assert result == {
        "item": {
            "catalog": "alma",
            "internal_id": "alma:desk",
            "quantity": "2",
            "base_option_id": "base-a",
            "add_on_option_ids": ["a", "b"],
        },
        "snapshotKeys": [
            "availability",
            "code",
            "configuration",
            "image_url",
            "name",
            "unit",
            "warnings",
        ],
    }


def test_mixed_quote_serializer_has_three_exact_commercial_branches():
    tarkett = {
        "catalog": "tarkett",
        "identity": {"code": "T-1", "ignored": "x"},
        "quantity": "0.5",
        "quantityRules": {
            "min": "0.000001",
            "step": "0.000001",
            "maxDecimals": 6,
            "max": "2",
        },
        "snapshot": {
            "name": "Tarkett",
            "code": "T-1",
            "image_url": "",
            "unit": "M2",
            "availability": "2",
            "configuration": "",
            "warnings": [],
        },
    }
    offiho = {
        "catalog": "offiho",
        "identity": {"inventory_key": "OFF-1", "ignored": "x"},
        "quantity": "2",
        "quantityRules": {
            "min": "0.001",
            "step": "0.001",
            "maxDecimals": 3,
            "max": "10",
        },
        "snapshot": {
            "name": "Offiho",
            "code": "OFF-1",
            "image_url": "",
            "unit": "PZA",
            "availability": "10",
            "configuration": "",
            "warnings": [],
        },
    }
    supplier_without_base = json.loads(
        supplier_line_source(catalog="cr-global", internal_id="cr-global:chair")
    )
    result = run_mixed_cart_js(
        f"""
      const lines = [
        createMixedCartLine({json.dumps(tarkett)}),
        createMixedCartLine({json.dumps(offiho)}),
        createMixedCartLine({json.dumps(supplier_without_base)})
      ];
      console.log(JSON.stringify(lines.map(toMixedQuoteItem)));
    """
    )
    assert result == [
        {"catalog": "tarkett", "code": "T-1", "quantity": "0.5"},
        {"catalog": "offiho", "inventory_key": "OFF-1", "quantity": "2"},
        {
            "catalog": "cr-global",
            "internal_id": "cr-global:chair",
            "quantity": "1",
            "add_on_option_ids": [],
        },
    ]


def test_catalog_list_is_frozen_and_complete():
    result = run_mixed_cart_js(
        """
      let mutation = "accepted";
      try { MIXED_CATALOGS.push("other"); } catch (error) { mutation = error.name; }
      console.log(JSON.stringify({catalogs: MIXED_CATALOGS, mutation}));
    """
    )
    assert result == {
        "catalogs": [
            "tarkett",
            "offiho",
            "cr-global",
            "sonara",
            "sunon",
            "alma",
            "lumbro",
        ],
        "mutation": "TypeError",
    }


def test_sparse_supplier_add_ons_are_rejected_before_key_or_payload_creation():
    result = run_mixed_cart_js(
        """
      const input = (add_on_option_ids) => ({
        catalog: "alma",
        identity: {internal_id: "alma:desk", base_option_id: "", add_on_option_ids},
        quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {name: "Desk", code: "AL-1", image_url: "", unit: "PZA", availability: "", configuration: "", warnings: []}
      });
      const partlySparse = ["addon-a", "addon-b"];
      delete partlySparse[1];
      const attempts = [
        () => mixedCartKey("alma", input(new Array(1)).identity),
        () => toMixedQuoteItem(createMixedCartLine(input(partlySparse)))
      ];
      console.log(JSON.stringify(attempts.map(run => {
        try { return {accepted: true, value: run()}; }
        catch (error) { return {accepted: false, message: error.message}; }
      })));
    """
    )
    assert result == [
        {"accepted": False, "message": "Add-on requerido"},
        {"accepted": False, "message": "Add-on requerido"},
    ]
    assert "[null]" not in json.dumps(result)


def test_identity_fields_must_be_own_and_prototype_pollution_is_not_consumed():
    result = run_mixed_cart_js(
        """
      const inheritedRequired = Object.create({code: "T-inherited"});
      const inheritedOptional = Object.create({
        base_option_id: "base-polluted", add_on_option_ids: ["addon-polluted"]
      });
      inheritedOptional.internal_id = "alma:desk";
      const nullPrototype = Object.create(null);
      nullPrototype.code = "T-null";
      const attempts = [
        () => mixedCartKey("tarkett", inheritedRequired),
        () => mixedCartKey("alma", inheritedOptional),
        () => mixedCartKey("tarkett", nullPrototype)
      ];
      const originalBase = Object.getOwnPropertyDescriptor(Object.prototype, "base_option_id");
      const originalAddOns = Object.getOwnPropertyDescriptor(Object.prototype, "add_on_option_ids");
      let pollutionResult;
      try {
        Object.prototype.base_option_id = "base-polluted";
        Object.prototype.add_on_option_ids = ["addon-polluted"];
        pollutionResult = mixedCartKey("alma", {internal_id: "alma:safe"});
      } finally {
        if (originalBase) Object.defineProperty(Object.prototype, "base_option_id", originalBase);
        else delete Object.prototype.base_option_id;
        if (originalAddOns) Object.defineProperty(Object.prototype, "add_on_option_ids", originalAddOns);
        else delete Object.prototype.add_on_option_ids;
      }
      console.log(JSON.stringify({
        attempts: attempts.map(run => {
          try { return {accepted: true, value: run()}; }
          catch (error) { return {accepted: false, message: error.message}; }
        }),
        pollutionResult,
        pollutionCleaned: !Object.hasOwn(Object.prototype, "base_option_id")
          && !Object.hasOwn(Object.prototype, "add_on_option_ids")
      }));
    """
    )
    assert result == {
        "attempts": [
            {"accepted": False, "message": "Identidad invalida"},
            {"accepted": False, "message": "Identidad invalida"},
            {"accepted": True, "value": "tarkett:T-null"},
        ],
        "pollutionResult": 'alma:["alma:safe","",[]]',
        "pollutionCleaned": True,
    }


def test_javascript_identity_validation_matches_actual_python_backend():
    rows = [
        {"catalog": "tarkett", "code": "\u2003T-1\u2003", "quantity": "1"},
        {
            "catalog": "offiho",
            "inventory_key": "OHE-405 NEGRO ALUFSEN",
            "quantity": "1",
        },
        {
            "catalog": "alma",
            "internal_id": "alma:desk|uno",
            "base_option_id": "base:a",
            "add_on_option_ids": ["𐀀", "\ue000", "addon-a"],
            "quantity": "1",
        },
        {"catalog": "tarkett", "code": "😀" * 1000, "quantity": "1"},
        {"catalog": "tarkett", "code": "😀" * 1001, "quantity": "1"},
        {"catalog": "tarkett", "code": "\ufeffT-FEFF", "quantity": "1"},
        {
            "catalog": "alma",
            "internal_id": "alma:desk",
            "base_option_id": "😀" * 500,
            "add_on_option_ids": ["addon-a"],
            "quantity": "1",
        },
        {
            "catalog": "alma",
            "internal_id": "alma:desk",
            "base_option_id": "😀" * 501,
            "add_on_option_ids": [],
            "quantity": "1",
        },
    ]

    python_results = []
    for row in rows:
        try:
            normalized = preflight_mixed_catalog_items([row])[0]
            python_results.append(
                {"accepted": True, "key": python_mixed_cart_key(normalized)}
            )
        except ValueError:
            python_results.append({"accepted": False})

    javascript_results = run_mixed_cart_js(
        f"const rows = {json.dumps(rows)};\n"
        + """
      const identityFor = (row) => {
        if (row.catalog === "tarkett") return {code: row.code};
        if (row.catalog === "offiho") return {inventory_key: row.inventory_key};
        return {
          internal_id: row.internal_id,
          base_option_id: row.base_option_id,
          add_on_option_ids: row.add_on_option_ids
        };
      };
      console.log(JSON.stringify(rows.map(row => {
        try { return {accepted: true, key: mixedCartKey(row.catalog, identityFor(row))}; }
        catch (error) { return {accepted: false}; }
      })));
    """
    )

    assert javascript_results == python_results
    assert python_results[3]["accepted"] is True  # 1000 astral code points
    assert python_results[4] == {"accepted": False}
    assert python_results[5] == {"accepted": False}  # FEFF is Cf, not Python strip
    assert python_results[6]["accepted"] is True  # 500 astral base-option code points
    assert python_results[7] == {"accepted": False}
