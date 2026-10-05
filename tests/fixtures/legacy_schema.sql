-- Schema of a production database from before schema versioning (captured 2026-10-06).
CREATE TABLE access_codes (
	id INTEGER NOT NULL, 
	code VARCHAR(32) NOT NULL, 
	label VARCHAR(64), 
	used_by BIGINT, 
	is_active BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	used_at DATETIME, 
	PRIMARY KEY (id)
);

CREATE TABLE api_stores (
	id INTEGER NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	api_key VARCHAR(128) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	daily_limit INTEGER NOT NULL, 
	orders_today INTEGER NOT NULL, 
	last_reset DATETIME, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE app_settings (
	"key" VARCHAR(64) NOT NULL, 
	value VARCHAR(128) NOT NULL, 
	PRIMARY KEY ("key")
);

CREATE TABLE "order_items" (
                    id INTEGER NOT NULL PRIMARY KEY,
                    order_id INTEGER NOT NULL,
                    product_id INTEGER,
                    product_name VARCHAR(128) NOT NULL,
                    quantity INTEGER NOT NULL,
                    FOREIGN KEY(order_id) REFERENCES orders (id),
                    FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE SET NULL
                );

CREATE TABLE order_status_history (
	id INTEGER NOT NULL, 
	order_id INTEGER NOT NULL, 
	old_status VARCHAR(32), 
	new_status VARCHAR(32) NOT NULL, 
	changed_by VARCHAR(64), 
	note TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(order_id) REFERENCES orders (id)
);

CREATE TABLE orders (
	id INTEGER NOT NULL, 
	order_id VARCHAR(16) NOT NULL, 
	user_id INTEGER NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	player_id VARCHAR(64) NOT NULL, 
	supplier_msg_id INTEGER, 
	notes TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, api_store_id INTEGER, settled_at TIMESTAMP, settled_by VARCHAR(128), customer_msg_id BIGINT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE TABLE pin_config (
	id INTEGER NOT NULL, 
	hashed_pin VARCHAR(256), 
	updated_at DATETIME NOT NULL, 
	invalidate_sessions BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE products (
	id INTEGER NOT NULL, 
	category VARCHAR(64) NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	price FLOAT NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, supplier_chat_id BIGINT, 
	PRIMARY KEY (id)
);

CREATE TABLE supplier_fulfillments (
	id INTEGER NOT NULL, 
	order_id INTEGER NOT NULL, 
	supplier_chat_id BIGINT NOT NULL, 
	category VARCHAR(64) NOT NULL, 
	items_snapshot TEXT NOT NULL, 
	status VARCHAR(9) NOT NULL, 
	changed_by VARCHAR(128), 
	supplier_msg_id BIGINT, 
	failure_note TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(order_id) REFERENCES orders (id)
);

CREATE TABLE user_order_limits (
	id INTEGER NOT NULL, 
	telegram_id BIGINT NOT NULL, 
	daily_limit INTEGER NOT NULL, 
	orders_today INTEGER NOT NULL, 
	last_reset DATETIME, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE users (
	id INTEGER NOT NULL, 
	telegram_id BIGINT NOT NULL, 
	username VARCHAR(64), 
	full_name VARCHAR(128), 
	auth_status VARCHAR(15) NOT NULL, 
	failed_attempts INTEGER NOT NULL, 
	locked_until DATETIME, 
	last_login DATETIME, 
	session_expires DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_access_codes_code ON access_codes (code);

CREATE UNIQUE INDEX ix_api_stores_api_key ON api_stores (api_key);

CREATE INDEX ix_orders_api_store_id ON orders (api_store_id);

CREATE UNIQUE INDEX ix_orders_order_id ON orders (order_id);

CREATE INDEX ix_orders_settled_at ON orders (settled_at);

CREATE INDEX ix_supplier_fulfillments_order_id ON supplier_fulfillments (order_id);

CREATE INDEX ix_supplier_fulfillments_supplier_chat_id ON supplier_fulfillments (supplier_chat_id);

CREATE UNIQUE INDEX ix_user_order_limits_telegram_id ON user_order_limits (telegram_id);

CREATE UNIQUE INDEX ix_users_telegram_id ON users (telegram_id);

