-- HOMEZ "대기 상품 정리" — listing_wizards soft delete(=ARCHIVED) 지원 (additive only).
-- 물리 삭제가 아니라 status='ARCHIVED' 전이 + 아래 스냅샷 컬럼만
-- 기록한다. 기존 행은 전부 NULL(삭제된 적 없음)로 시작한다.
ALTER TABLE listing_wizards
    ADD COLUMN deleted_at DATETIME;
ALTER TABLE listing_wizards
    ADD COLUMN deleted_by_user_id INTEGER;
ALTER TABLE listing_wizards
    ADD COLUMN delete_reason VARCHAR(500);
ALTER TABLE listing_wizards
    ADD COLUMN deletion_request_id VARCHAR(160);
ALTER TABLE listing_wizards
    ADD COLUMN status_before_archive VARCHAR(30);
ALTER TABLE listing_wizards
    ADD COLUMN restored_at DATETIME;
ALTER TABLE listing_wizards
    ADD COLUMN restored_by_user_id INTEGER;
