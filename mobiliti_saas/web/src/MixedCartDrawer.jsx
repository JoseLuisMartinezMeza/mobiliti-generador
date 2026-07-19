import React, { useEffect, useRef, useState } from "react";
import { ImageOff, ShoppingCart, Trash2, X } from "lucide-react";
import { validateLineQuantity } from "./mixedCart.js";

const CATALOG_LABELS = Object.freeze({
  tarkett: "Tarkett",
  offiho: "Offiho",
  "cr-global": "CR Global",
  sonara: "Sonara",
  sunon: "Sunon",
  alma: "ALMA",
  lumbro: "Lumbro",
});

const CUSTOMER_FIELDS = Object.freeze([
  ["Proyecto *", "proyecto", "text"],
  ["Cliente *", "cliente", "text"],
  ["Correo *", "correo", "email"],
  ["Telefono *", "telefono", "tel"],
  ["Direccion *", "direccion", "text"],
  ["Razon social *", "razon_social", "text"],
]);

function handleMixedCartEscape(event, busy, onClose) {
  if (event.key !== "Escape" || busy) return false;
  event.preventDefault();
  onClose();
  return true;
}

function submitMixedDrawerDrafts({
  event,
  lines,
  quantityDrafts,
  setErrors,
  focusFirst,
  onSubmit,
}) {
  event.preventDefault();
  const errors = {};
  const committedLines = lines.map((line) => {
    try {
      const quantity = validateLineQuantity(line, quantityDrafts[line.key] ?? line.quantity);
      return { ...line, quantity };
    } catch (quantityError) {
      errors[line.key] = quantityError.message || "Cantidad invalida";
      return null;
    }
  });
  setErrors(errors);
  if (Object.keys(errors).length) {
    focusFirst(Object.keys(errors)[0]);
    return false;
  }
  onSubmit(event, committedLines);
  return true;
}

export default function MixedCartDrawer({
  lines,
  open,
  form,
  busy,
  error,
  notice,
  onClose,
  onFieldChange,
  onQuantityChange,
  onRemove,
  onSubmit,
}) {
  const [quantityDrafts, setQuantityDrafts] = useState({});
  const [quantityErrors, setQuantityErrors] = useState({});
  const previousCommittedRef = useRef({});
  const drawerRef = useRef(null);
  const quantityInputRefs = useRef({});
  const previousFocusRef = useRef(null);
  const onCloseRef = useRef(onClose);
  const busyRef = useRef(busy);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);

  useEffect(() => {
    const visibleKeys = new Set(lines.map((line) => line.key));
    setQuantityDrafts((current) => {
      const next = {};
      lines.forEach((line) => {
        const previousCommitted = previousCommittedRef.current[line.key];
        const currentDraft = current[line.key];
        const userHasDiverged = currentDraft !== undefined
          && previousCommitted !== undefined
          && currentDraft !== previousCommitted;
        next[line.key] = userHasDiverged ? currentDraft : line.quantity;
      });
      return next;
    });
    setQuantityErrors((current) => Object.fromEntries(
      Object.entries(current).filter(([key]) => visibleKeys.has(key)),
    ));
    previousCommittedRef.current = Object.fromEntries(
      lines.map((line) => [line.key, line.quantity]),
    );
  }, [lines]);

  useEffect(() => {
    if (!open) return undefined;
    previousFocusRef.current = document.activeElement;
    drawerRef.current?.focus();
    function handleDrawerKeyDown(event) {
      if (event.key === "Escape") {
        handleMixedCartEscape(event, busyRef.current, onCloseRef.current);
        return;
      }
      if (event.key === "Tab") {
        const focusable = Array.from(drawerRef.current?.querySelectorAll(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) || []);
        if (!focusable.length) {
          event.preventDefault();
          drawerRef.current?.focus();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === drawerRef.current)) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }
    window.addEventListener("keydown", handleDrawerKeyDown);
    return () => {
      window.removeEventListener("keydown", handleDrawerKeyDown);
      previousFocusRef.current?.focus?.();
    };
  }, [open]);

  function commitQuantity(line) {
    const draft = quantityDrafts[line.key] ?? line.quantity;
    try {
      const normalized = validateLineQuantity(line, draft);
      onQuantityChange(line.key, normalized);
      setQuantityDrafts((current) => ({ ...current, [line.key]: normalized }));
      setQuantityErrors((current) => {
        const next = { ...current };
        delete next[line.key];
        return next;
      });
    } catch (quantityError) {
      setQuantityErrors((current) => ({
        ...current,
        [line.key]: quantityError.message || "Cantidad invalida",
      }));
    }
  }

  function handleDrawerSubmit(event) {
    submitMixedDrawerDrafts({
      event,
      lines,
      quantityDrafts,
      setErrors: setQuantityErrors,
      focusFirst: (key) => quantityInputRefs.current[key]?.focus(),
      onSubmit,
    });
  }

  return (
    <>
      {open ? (
        <button
          className="mixed-cart-overlay"
          type="button"
          aria-label="Cerrar carrito"
          disabled={busy}
          onClick={onClose}
        />
      ) : null}
      <aside
        className={`mixed-cart-drawer ${open ? "open" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-hidden={!open}
        aria-label="Carrito de todos los catalogos"
        tabIndex="-1"
        ref={drawerRef}
      >
        <div className="mixed-cart-title">
          <div><ShoppingCart size={22} /><h2>Carrito</h2><span>{lines.length}</span></div>
          <button type="button" onClick={onClose} aria-label="Cerrar carrito" disabled={busy}>
            <X size={20} />
          </button>
        </div>

        {!lines.length ? (
          <p className="mixed-cart-empty">Selecciona productos de cualquiera de los siete catalogos.</p>
        ) : null}
        <div className="mixed-cart-lines">
          {lines.map((line) => (
            <article className="mixed-cart-line" key={line.key}>
              <div className="mixed-cart-line-image">
                {line.snapshot.image_url ? (
                  <img src={line.snapshot.image_url} alt="" loading="lazy" />
                ) : <ImageOff size={22} aria-label="Sin imagen" />}
              </div>
              <div className="mixed-cart-line-copy">
                <strong>{line.snapshot.name || line.snapshot.code}</strong>
                {line.snapshot.configuration ? (
                  <span className="mixed-cart-line-configuration">{line.snapshot.configuration}</span>
                ) : null}
                <small>{line.snapshot.code} · {CATALOG_LABELS[line.catalog] || line.catalog}</small>
                {line.snapshot.availability ? <small>{line.snapshot.availability}</small> : null}
                {(line.snapshot.warnings || []).map((warning, index) => (
                  <em key={`${line.key}-warning-${index}`}>{warning || "Codigo por verificar"}</em>
                ))}
              </div>
              <label className="mixed-cart-quantity">
                <span>Cantidad</span>
                <input
                  ref={(element) => { quantityInputRefs.current[line.key] = element; }}
                  inputMode="decimal"
                  value={quantityDrafts[line.key] ?? line.quantity}
                  disabled={busy}
                  aria-invalid={Boolean(quantityErrors[line.key])}
                  onChange={(event) => setQuantityDrafts((current) => ({
                    ...current,
                    [line.key]: event.target.value,
                  }))}
                  onBlur={() => commitQuantity(line)}
                />
                {quantityErrors[line.key] ? <small role="alert">{quantityErrors[line.key]}</small> : null}
              </label>
              <button
                className="mixed-cart-remove"
                type="button"
                disabled={busy}
                onClick={() => onRemove(line.key)}
                aria-label={`Quitar ${line.snapshot.name || line.snapshot.code}`}
              >
                <Trash2 size={18} />
              </button>
            </article>
          ))}
        </div>

        <form className="mixed-quote-form" onSubmit={handleDrawerSubmit}>
          {CUSTOMER_FIELDS.map(([label, field, type]) => (
            <label key={field}>
              <span>{label}</span>
              <input
                name={field}
                type={type}
                required
                disabled={busy}
                value={form[field]}
                onChange={(event) => onFieldChange(field, event.target.value)}
              />
            </label>
          ))}
          <label>
            <span>Moneda de cotizacion</span>
            <select
              value={form.quote_currency}
              disabled={busy}
              onChange={(event) => onFieldChange("quote_currency", event.target.value)}
            >
              {['MXN', 'USD', 'EUR'].map((currency) => (
                <option key={currency} value={currency}>{currency}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Descuento Tarkett y Offiho (%)</span>
            <input
              type="number"
              min="0"
              max="100"
              step="0.01"
              disabled={busy}
              value={form.descuento}
              onChange={(event) => onFieldChange("descuento", event.target.value)}
            />
            <small>CR Global, Sonara, Sunon, ALMA y Lumbro conservan precio neto sin descuento adicional.</small>
          </label>
          {error ? <div className="error-line" role="alert">{error}</div> : null}
          {notice ? <div className="notice-line" role="status" aria-live="polite">{notice}</div> : null}
          <button className="primary-action" disabled={busy || !lines.length} type="submit">
            {busy ? "Cotizando..." : "Cotizar todos los catalogos"}
          </button>
        </form>
      </aside>
    </>
  );
}
