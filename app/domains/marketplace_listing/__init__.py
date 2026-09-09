"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/__init__.py

HOMEZ 채널별 판매 방식(Fulfillment Mode) 선택 Domain.

판매 방식은 Product 전역 속성이 아니라 (ProductCandidate ×
MarketplaceAccount) 단위의 Listing 속성이다. app/domains/coupang과
동일한 설계 원칙(FK 없음, 논리 참조 컬럼만, Numeric은 이 Domain에
두지 않고 Decision AI 경계로만 전달)을 따른다.
=========================================================
"""
