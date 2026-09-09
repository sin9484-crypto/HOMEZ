"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/adapters/__init__.py

채널별 Fulfillment Adapter — 내부 표준 enum(FulfillmentMode)을 채널별
외부 API 필드 구조로 변환한다. 이 패키지의 어떤 파일도 requests/httpx
등 네트워크 라이브러리를 import하지 않는다(실제 API 호출 없음,
app/domains/coupang/gateway.py::CoupangDryRunGateway와 동일한 원칙).
=========================================================
"""
