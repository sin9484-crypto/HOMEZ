"""
=========================================================
Homez OS

File : app/domains/external_call/constants.py

2026-10-16 — 쿠팡·온채널 같은 **바깥 서비스를 부르는 일의 허용(승인) 규칙**. 개발 중 사람이 직접 열던 "호출 예산"을 앱 기능으로
옮긴 것이다. 허용이 없으면 해당 기능은 바깥으로 요청을 보내지 않고, 승인 센터에 "허용 요청"이 올라간다.
=========================================================
"""


class ExternalSystem:
    COUPANG = "COUPANG"
    ONCH = "ONCH"

    ALL = (COUPANG, ONCH)
    LABELS = {COUPANG: "쿠팡", ONCH: "온채널"}


class CallKind:
    READ = "READ"      # 조회(GET) — 바깥 상태를 읽기만 한다
    WRITE = "WRITE"    # 변경(POST/PUT 등) — 바깥 상태를 바꾼다

    ALL = (READ, WRITE)
    LABELS = {READ: "조회", WRITE: "변경"}


class CallPurpose:
    """무엇을 위한 호출인가 — 허용은 목적별로 준다."""

    ORDER_DETECTION = "ORDER_DETECTION"            # 신규 주문 자동 감지(쿠팡 주문 조회, 5분마다)
    ORDER_TRACKING = "ORDER_TRACKING"              # 진행 중 주문·배송 상태 확인(쿠팡 발주서 단건 조회, 10분마다)
    INVOICE_UPLOAD = "INVOICE_UPLOAD"              # 쿠팡 송장 등록(발주서 조회·송장 전송)
    PRODUCT_OUTBOUND_DAYS = "PRODUCT_OUTBOUND_DAYS"  # 등록 상품 기준 출고일 변경(상품 조회·수정)
    ANY = "ANY"                                    # 모든 목적(조회·변경 구분은 유지)

    ALL = (ORDER_DETECTION, ORDER_TRACKING, INVOICE_UPLOAD, PRODUCT_OUTBOUND_DAYS, ANY)
    LABELS = {
        ORDER_DETECTION: "신규 주문 자동 감지",
        ORDER_TRACKING: "주문·배송 상태 확인",
        INVOICE_UPLOAD: "쿠팡 송장 등록",
        PRODUCT_OUTBOUND_DAYS: "상품 출고일 변경",
        ANY: "모든 목적",
    }
    # 허용이 없으면 **막는** 목적(나머지는 기록만 한다 — 판매중지·주문 취소 같은 안전 장치는 허용을 기다리면 안 된다).
    ENFORCED = frozenset({ORDER_DETECTION, ORDER_TRACKING, INVOICE_UPLOAD, PRODUCT_OUTBOUND_DAYS})


class AllowanceMode:
    COUNTED = "COUNTED"    # 횟수를 정해 준다(쓸 때마다 줄고 0이 되면 끝)
    STANDING = "STANDING"  # 기간 동안 상시 허용(시간당 상한 안에서)

    ALL = (COUNTED, STANDING)


class AllowanceStatus:
    PENDING = "PENDING"      # 기능이 요청했고 아직 결정 전
    ACTIVE = "ACTIVE"        # 허용 중
    EXHAUSTED = "EXHAUSTED"  # 횟수를 다 씀
    REVOKED = "REVOKED"      # 사용자가 취소
    EXPIRED = "EXPIRED"      # 기간이 지남
    REJECTED = "REJECTED"    # 사용자가 거절

    ALL = (PENDING, ACTIVE, EXHAUSTED, REVOKED, EXPIRED, REJECTED)


class LogOutcome:
    ALLOWED = "ALLOWED"      # 허용을 쓰고 요청을 보냈다(결과는 아래로 갱신)
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    LOGGED = "LOGGED"        # 허용 대상이 아니라 기록만 했다

    ALL = (ALLOWED, SUCCEEDED, FAILED, LOGGED)


MAX_COUNTED_GRANT = 50
MAX_STANDING_HOURS = 24 * 30
MAX_PER_HOUR = 120
DEFAULT_ORDER_DETECTION_PER_HOUR = 14   # 5분마다 1회 = 시간당 12회 + 여유
DEFAULT_ORDER_TRACKING_PER_HOUR = 30    # 10분마다 진행 중 주문 수만큼(주문 5건 = 시간당 30회)
DEFAULT_STANDING_HOURS = 24
