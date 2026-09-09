-- Purpose: V7 Gate 9(2026-08-16) — "완전히 새 PC에서 첫 실행" 클린 설치
-- 검증 중 발견한 Critical 결함 수정: companies/users/roles/permissions/
-- role_permissions/categories/brands/suppliers/products/marketplaces
-- 10개 핵심(Core) 테이블을 만드는 Migration 파일이 저장소 어디에도
-- 없었다.
-- Source of Truth: 실제 운영 homez.db(읽기 전용 `sqlite_master.sql`
-- 덤프, 2026-08-16) — 아래 이유로 SQLAlchemy 모델의
-- `CreateTable(model.__table__).compile(dialect=sqlite)`을 쓰지 않고
-- 실제 운영 DB의 현재 스키마를 그대로 옮겼다.
--
-- 배경(Gate 9 클린 설치 재현 중 발견): 공식 부트스트랩 경로
-- (`app/desktop/main.py` → `app/database/bootstrap.py::
-- bootstrap_environment()`)는 `MigrationRunner.apply_pending()`만
-- 호출한다. 기존 22개 Migration 파일 전체를 완전히 새 빈 SQLite
-- 파일에 순서대로 적용해도 `companies`/`users`/`roles`/`permissions`
-- 테이블이 전혀 생기지 않아, 그 직후 공식 최초 관리자 생성 흐름
-- (`app/core/first_admin_setup.py::atomic_create_first_admin()`)이
-- `sqlite3.OperationalError: no such table: users`로 즉시 실패함을
-- 실측으로 확인했다(scratchpad/gate9_clean_install_bootstrap_check.log).
-- `app/database/init_db.py::initialize_database()`가
-- `Base.metadata.create_all()`로 이 10개 테이블을 포함한 모델들을
-- 선언하고 있지만, 저장소 어디에서도 호출되지 않는 죽은 코드다(grep
-- 재확인, 공식 부트스트랩 경로는 이 함수를 전혀 참조하지 않는다).
-- 즉 실제 운영 DB의 이 10개 테이블은 이 Migration 시스템이 도입되기
-- 이전(2026-08-01 이전으로 추정, `schema_migrations` 최초
-- BACKFILLED 이력이 2026-08-01)에 다른 경로로 이미 만들어져 있었고,
-- 그 이후로는 파일 기반 Migration으로 재현할 방법이 없는 상태로
-- 남아 있었다 — 실제 homez.db를 백업/복원하거나(기존 백업 엔진은
-- 파일 전체 스냅샷이라 이 문제와 무관), 클린 설치를 하지 않는 한
-- 드러나지 않는 결함이었다.
--
-- 왜 SQLAlchemy 모델을 그대로 컴파일하지 않았는가: `app/domains/
-- permission/model.py`의 `Permission` 모델은 실제 운영 DB의
-- `permissions` 테이블에 없는 `created_at`/`updated_at` 컬럼을
-- 선언하고 있고, 반대로 실제 테이블에 있는 `company_id` 컬럼은
-- 선언하지 않고 있다(Model↔DB 드리프트, 이번 Gate 9에서 발견 —
-- 수정하지 않았다, 별도 승인 필요 CTO 확인 사항으로 아래 보고서에
-- 기록). 이 드리프트 상태에서 모델을 컴파일하면 실제 운영 DB와
-- 다른(그리고 이미 알려진 버그가 있는) 스키마를 새로 만들게 되므로,
-- 대신 실제 운영 DB의 현재 스키마를 있는 그대로 재현하는 쪽을
-- 선택했다 — "지금 실제로 동작 중인 스키마와 클린 설치가 반드시
-- 일치해야 한다"는 원칙을 "모델과 일치해야 한다"는 원칙보다
-- 우선했다(모델 자체의 드리프트 수정은 이번 Gate 9의 범위 밖이다).
--
-- 검증: 완전히 새 임시 SQLite 파일에 기존 22개 Migration + 이 파일을
-- 순서대로 적용한 뒤 `atomic_create_first_admin()`을 실제로 호출해
-- 성공(회사+관리자 계정 생성)함을 확인했다(scratchpad/
-- gate9_clean_install_bootstrap_check_v2.log). 실제 운영 homez.db에는
-- 적용하지 않았다 — 이 파일의 대상 10개 테이블이 이미 전부 존재하는
-- 운영 DB에서는 `MigrationRunner.diagnose()`가 이 파일을
-- `backfill_needed`(이력만 채움, 실제 DDL 미실행)로 정확히 분류함을
-- 읽기 전용으로 확인했다(order inversion 없음 — 현재 운영 DB의 최신
-- APPLIED 파일 `20260814_01_...`보다 이 파일명이 사전순으로 뒤에
-- 온다).

BEGIN;

-- ====================================================
-- 1) companies
-- ====================================================
CREATE TABLE companies (
	id INTEGER NOT NULL,
	name VARCHAR(100) NOT NULL,
	business_number VARCHAR(30),
	ceo VARCHAR(100),
	phone VARCHAR(30),
	email VARCHAR(150),
	address VARCHAR(300),
	active BOOLEAN NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
);
CREATE INDEX ix_companies_id ON companies (id);

-- ====================================================
-- 2) users
-- ====================================================
CREATE TABLE users (
	id INTEGER NOT NULL,
	company_id INTEGER,
	role_id INTEGER,
	username VARCHAR(100) NOT NULL,
	email VARCHAR(200) NOT NULL,
	password VARCHAR(255) NOT NULL,
	name VARCHAR(100),
	phone VARCHAR(30),
	active BOOLEAN NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(company_id) REFERENCES companies (id),
	FOREIGN KEY(role_id) REFERENCES roles (id)
);
CREATE INDEX ix_users_id ON users (id);
CREATE UNIQUE INDEX ix_users_email ON users (email);
CREATE UNIQUE INDEX ix_users_username ON users (username);

-- ====================================================
-- 3) roles
-- ====================================================
CREATE TABLE roles (
	id INTEGER NOT NULL,
	company_id INTEGER,
	name VARCHAR(100) NOT NULL,
	code VARCHAR(50) NOT NULL,
	description VARCHAR(255),
	active BOOLEAN NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(company_id) REFERENCES companies (id)
);
CREATE INDEX ix_roles_id ON roles (id);
CREATE UNIQUE INDEX ix_roles_code ON roles (code);

-- ====================================================
-- 4) permissions
-- ====================================================
CREATE TABLE permissions (
	id INTEGER NOT NULL,
	company_id INTEGER,
	name VARCHAR(100) NOT NULL,
	code VARCHAR(100) NOT NULL,
	description VARCHAR(255),
	active BOOLEAN NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(company_id) REFERENCES companies (id)
);
CREATE INDEX ix_permissions_id ON permissions (id);
CREATE UNIQUE INDEX ix_permissions_code ON permissions (code);

-- ====================================================
-- 5) role_permissions
-- ====================================================
CREATE TABLE role_permissions (
	id INTEGER NOT NULL,
	role_id INTEGER NOT NULL,
	permission_id INTEGER NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_role_permission UNIQUE (role_id, permission_id),
	FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE CASCADE,
	FOREIGN KEY(permission_id) REFERENCES permissions (id) ON DELETE CASCADE
);
CREATE INDEX ix_role_permissions_id ON role_permissions (id);

-- ====================================================
-- 6) categories
-- ====================================================
CREATE TABLE categories (
	id INTEGER NOT NULL,
	company_id INTEGER,
	parent_id INTEGER,
	name VARCHAR(100) NOT NULL,
	code VARCHAR(50),
	description VARCHAR(300),
	sort_order INTEGER NOT NULL,
	active BOOLEAN NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(company_id) REFERENCES companies (id),
	FOREIGN KEY(parent_id) REFERENCES categories (id),
	UNIQUE (name),
	UNIQUE (code)
);
CREATE INDEX ix_categories_id ON categories (id);

-- ====================================================
-- 7) brands
-- ====================================================
CREATE TABLE brands (
	id INTEGER NOT NULL,
	company_id INTEGER,
	name VARCHAR(100) NOT NULL,
	code VARCHAR(50),
	description VARCHAR(300),
	logo VARCHAR(500),
	active BOOLEAN NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(company_id) REFERENCES companies (id),
	UNIQUE (name),
	UNIQUE (code)
);
CREATE INDEX ix_brands_id ON brands (id);

-- ====================================================
-- 8) suppliers
-- ====================================================
CREATE TABLE suppliers (
	id INTEGER NOT NULL,
	company_id INTEGER,
	name VARCHAR(150) NOT NULL,
	business_number VARCHAR(30),
	ceo VARCHAR(100),
	phone VARCHAR(30),
	email VARCHAR(150),
	address VARCHAR(300),
	active BOOLEAN NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(company_id) REFERENCES companies (id),
	UNIQUE (name)
);
CREATE INDEX ix_suppliers_id ON suppliers (id);

-- ====================================================
-- 9) products
-- ====================================================
CREATE TABLE products (
	id INTEGER NOT NULL,
	company_id INTEGER,
	category_id INTEGER,
	brand_id INTEGER,
	supplier_id INTEGER,
	name VARCHAR(200) NOT NULL,
	sku VARCHAR(100),
	barcode VARCHAR(100),
	description TEXT,
	cost_price FLOAT NOT NULL,
	sale_price FLOAT NOT NULL,
	stock INTEGER NOT NULL,
	active BOOLEAN NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(company_id) REFERENCES companies (id),
	FOREIGN KEY(category_id) REFERENCES categories (id),
	FOREIGN KEY(brand_id) REFERENCES brands (id),
	FOREIGN KEY(supplier_id) REFERENCES suppliers (id),
	UNIQUE (sku),
	UNIQUE (barcode)
);
CREATE INDEX ix_products_id ON products (id);

-- ====================================================
-- 10) marketplaces
-- ====================================================
CREATE TABLE marketplaces (
	id INTEGER NOT NULL,
	company_id INTEGER,
	name VARCHAR(100) NOT NULL,
	code VARCHAR(50) NOT NULL,
	api_url VARCHAR(500),
	api_key VARCHAR(500),
	api_secret VARCHAR(500),
	active BOOLEAN NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(company_id) REFERENCES companies (id),
	UNIQUE (name),
	UNIQUE (code)
);
CREATE INDEX ix_marketplaces_id ON marketplaces (id);

COMMIT;
