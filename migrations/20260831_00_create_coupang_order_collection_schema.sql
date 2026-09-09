-- HOMEZ V7 work 3: read-only Coupang order collection persistence.
-- This migration is a draft and must not be applied to development/production DBs.
BEGIN;

CREATE TABLE order_channel_fulfillments (
    id INTEGER NOT NULL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    store_connection_id INTEGER NOT NULL,
    order_id INTEGER,
    channel_order_id VARCHAR(150) NOT NULL,
    shipment_box_id VARCHAR(150) NOT NULL,
    raw_status VARCHAR(50) NOT NULL,
    ordered_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_order_fulfillment_external_identity UNIQUE
        (company_id, store_connection_id, channel_order_id, shipment_box_id)
);
CREATE INDEX ix_order_channel_fulfillments_id ON order_channel_fulfillments (id);
CREATE INDEX ix_order_channel_fulfillments_company_id ON order_channel_fulfillments (company_id);
CREATE INDEX ix_order_channel_fulfillments_store_connection_id ON order_channel_fulfillments (store_connection_id);
CREATE INDEX ix_order_channel_fulfillments_order_id ON order_channel_fulfillments (order_id);
CREATE INDEX ix_order_channel_fulfillments_channel_order_id ON order_channel_fulfillments (channel_order_id);
CREATE INDEX ix_order_channel_fulfillments_shipment_box_id ON order_channel_fulfillments (shipment_box_id);

CREATE TABLE unresolved_order_items (
    id INTEGER NOT NULL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    fulfillment_id INTEGER NOT NULL,
    channel_item_id VARCHAR(100) NOT NULL,
    vendor_item_id VARCHAR(100) NOT NULL,
    channel_sku VARCHAR(150) NOT NULL,
    product_name_snapshot VARCHAR(300) NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price NUMERIC(18, 4) NOT NULL,
    order_price NUMERIC(18, 4) NOT NULL,
    currency_code VARCHAR(3) NOT NULL,
    status VARCHAR(30) NOT NULL,
    resolved_order_item_id INTEGER,
    created_at DATETIME NOT NULL,
    resolved_at DATETIME,
    CONSTRAINT uq_unresolved_order_item_external_identity UNIQUE
        (company_id, fulfillment_id, channel_item_id)
);
CREATE INDEX ix_unresolved_order_items_id ON unresolved_order_items (id);
CREATE INDEX ix_unresolved_order_items_company_id ON unresolved_order_items (company_id);
CREATE INDEX ix_unresolved_order_items_fulfillment_id ON unresolved_order_items (fulfillment_id);
CREATE INDEX ix_unresolved_order_items_vendor_item_id ON unresolved_order_items (vendor_item_id);
CREATE INDEX ix_unresolved_order_items_channel_sku ON unresolved_order_items (channel_sku);
CREATE INDEX ix_unresolved_order_items_status ON unresolved_order_items (status);
CREATE INDEX ix_unresolved_order_items_resolved_order_item_id ON unresolved_order_items (resolved_order_item_id);

CREATE TABLE order_collection_cursors (
    id INTEGER NOT NULL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    store_connection_id INTEGER NOT NULL,
    channel_status VARCHAR(30) NOT NULL,
    last_successful_to DATETIME,
    run_status VARCHAR(20) NOT NULL,
    lock_token VARCHAR(64),
    locked_at DATETIME,
    last_error_code VARCHAR(50),
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_order_collection_cursor_scope UNIQUE
        (company_id, store_connection_id, channel_status)
);
CREATE INDEX ix_order_collection_cursors_id ON order_collection_cursors (id);
CREATE INDEX ix_order_collection_cursors_company_id ON order_collection_cursors (company_id);
CREATE INDEX ix_order_collection_cursors_store_connection_id ON order_collection_cursors (store_connection_id);
CREATE INDEX ix_order_collection_cursors_run_status ON order_collection_cursors (run_status);

CREATE TABLE order_sku_resolutions (
    id INTEGER NOT NULL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    store_connection_id INTEGER NOT NULL,
    channel_sku VARCHAR(150) NOT NULL,
    inventory_sku_id INTEGER NOT NULL,
    created_by INTEGER NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_order_sku_resolution_scope UNIQUE
        (company_id, store_connection_id, channel_sku)
);
CREATE INDEX ix_order_sku_resolutions_id ON order_sku_resolutions (id);
CREATE INDEX ix_order_sku_resolutions_company_id ON order_sku_resolutions (company_id);
CREATE INDEX ix_order_sku_resolutions_store_connection_id ON order_sku_resolutions (store_connection_id);
CREATE INDEX ix_order_sku_resolutions_inventory_sku_id ON order_sku_resolutions (inventory_sku_id);

COMMIT;
