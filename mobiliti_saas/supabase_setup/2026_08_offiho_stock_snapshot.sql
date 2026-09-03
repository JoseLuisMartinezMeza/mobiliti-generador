-- Permite publicar el inventario Offiho en el mismo snapshot durable de Tarkett.
-- La migracion solo amplia la restriccion; no modifica ni elimina datos existentes.

ALTER TABLE saas_supplier_catalog_snapshots
    DROP CONSTRAINT IF EXISTS saas_supplier_catalog_snapshots_supplier_check;

ALTER TABLE saas_supplier_catalog_snapshots
    ADD CONSTRAINT saas_supplier_catalog_snapshots_supplier_check
    CHECK (supplier IN ('tarkett', 'offiho'));
