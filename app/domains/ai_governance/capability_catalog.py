"""
=========================================================
Homez OS

File : app/domains/ai_governance/capability_catalog.py

AI Capability Registry(CA-5, 2026-08-21 CTO 지시; AG-0~AG-2,
2026-08-21 후속 지시로 확장) — 모든 AI/규칙 기반 판단 기능의 역할
계약. 각 항목은 실제 코드를 직접 읽고 확인한 현재 구현 그대로
등록한다(존재하지 않는 기능을 미리 등록하지 않는다, FAKE_PROVIDER를
GENERATIVE_AI로 자칭하지 않는다).

`active=True`인 항목만 `AICapabilityRegistryService.require_active_
capability()`를 통과한다 — 역할 계약이 없거나 비활성인 호출은
차단된다. 이 카탈로그의 `max_automation_level`은 그 capability가
스스로 주장하는 상한일 뿐, 실제 실행 허용 여부(execution_allowed)는
언제나 각 도메인의 SafetyService/Permission/EStop/Approval Service가
최종 결정한다 — 이 레지스트리는 그 결정을 대신하지 않는다.

AG-0(2026-08-21) 핵심 원칙 — "AI Capability Registry는 핵심 CRUD
Service 전체의 on/off 스위치가 아니다": inventory.reserve/release/
consume/restock, pricing.request_price_change, order.collect_
channel_order, shipment.create_shipment, return_order.create_
return_order, settlement.confirm_deposit, guides.list_guides,
orchestration.get_summary는 전부 "사람이 직접 수행하는 핵심 업무
CRUD"이지 AI 판단이 아니다 — 이전 라운드에서 이 8곳에 실수로
capability 게이트를 걸었던 것을 전부 되돌렸다(app/domains/inventory/
service.py, pricing/service.py, order/service.py, shipment/
service.py, return_order/service.py, settlement/service.py,
guides/service.py, orchestration/dashboard_service.py 참고).
Gate AI-F1~F2(2026-08-22)에서 PRICING_INVENTORY/ORDER_SHIPMENT_RETURN/
SETTLEMENT/OPERATIONS_COORDINATION/USER_GUIDANCE 5개 capability
전부에 실제 읽기 전용 분석·추천 로직이 연결됐다(각 `provider_status`/
`actual_entry_points`가 실제 Service 경로를 가리킨다 — 전부 NO_AI/
RULE_ENGINE, 생성형 모델 호출 없음). 다만 아직 실 HTTP Router와 UI가
연결되지 않은 항목이 있다 — 그 경우 `unimplemented_dependencies`에
정직하게 남아 있다. 실제 CRUD(입금 확정·발주·배송·반품 상태 전이 등)는
여전히 각자 기존 Router/Permission/EStop 경계로만 동작하며 이
capability들과 무관하다(AG-0 원칙 유지).
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from app.domains.ai_governance.constants import AutomationLevel
from app.domains.ai_governance.constants import CapabilityCode
from app.domains.ai_governance.constants import CapabilityType

CAPABILITY_CATALOG_VERSION = "2.2.0"
# 2.1.0(2026-09-07): PRODUCT_DISCOVERY의 forbidden_operations에서
# "실제 외부 트렌드 API 호출"을 해제(NaverDataLabTrendAdapter 정식
# 승인, CapabilityType.EXTERNAL_DATA_PROVIDER 신규 추가).
# 2.2.0(2026-09-07 후속): 네이버가 개발자센터(openapi.naver.com)를
# NAVER API HUB(naverapihub.apigw.ntruss.com)로 이관했음이 확인되어
# PRODUCT_DISCOVERY의 엔드포인트·인증 헤더·external_provider 표기를
# 전부 API HUB 기준으로 재작성했다. 동시에 사용자 지시에 따라
# provider_status의 "REAL_PROVIDER_AVAILABLE" 단일 표기를 "코드
# 존재/API HUB 대응 완료/실제 호출 검증 완료" 3단계로 명시적으로
# 분리한다 — 지금 시점엔 앞의 둘만 TRUE이고, 세 번째는 사용자가
# 실제 키를 등록해 최소 1회 실 호출에 성공해야만 TRUE로 바뀐다.


@dataclass(frozen=True)
class CapabilityContract:

    capability_code: str
    display_name: str
    capability_type: str
    purpose: str
    allowed_operations: tuple[str, ...]
    forbidden_operations: tuple[str, ...]
    required_inputs: tuple[str, ...]
    allowed_basis: str
    decision_criteria: str
    missing_data_handling: str
    required_permission: str | None
    max_automation_level: str
    user_approval_condition: str
    execution_limit: str | None
    stop_condition: str
    external_provider: str | None
    model_prompt_policy_version: str
    audit_fields: tuple[str, ...] = field(
        default=("capability_code", "profile_version", "company_id", "entity_id"),
    )
    active: bool = True
    implementation_reference: str = ""

    # AG-2(2026-08-21) 신규 필드 — 13개 역할을 더 상세한 계약으로
    # 확정한다. 기존 필드(위)는 CA-5에서 이미 검증된 그대로 유지한다
    # (불필요한 이름 변경으로 인한 회귀 위험을 피한다).
    display_name_en: str = ""
    responsibilities: tuple[str, ...] = ()
    allowed_evidence_sources: tuple[str, ...] = ()
    # 이 capability가 반환할 수 있는 AIResultEnvelope.result_type 값
    # 집합(app/domains/ai_governance/constants.py::AIResultType 중).
    output_contract: tuple[str, ...] = ()
    # 실제 코드 경로 목록(구조화) — implementation_reference(자유
    # 텍스트, 하위호환용 유지)와 별개로 테스트가 기계적으로 순회할
    # 수 있게 튜플로도 보관한다. 실제로 연결된 곳이 없으면 빈 튜플
    # (guessing 금지 — provider_status가 그 이유를 설명한다).
    actual_entry_points: tuple[str, ...] = ()
    # 이 역할이 실제로 무엇으로 동작하는지 정직하게: 예)
    # "NO_REAL_PROVIDER — Fixture만", "NOT_IMPLEMENTED — 연결된 코드
    # 없음", "NO_AI — 순수 계산/규칙".
    provider_status: str = ""
    # 이 역할을 완전히 구현하려면 아직 없는 것(예: 실 LLM Provider,
    # 실제 계산 로직 자체, 실 HTTP 라우터).
    unimplemented_dependencies: tuple[str, ...] = ()


AI_CAPABILITY_CATALOG: list[CapabilityContract] = [
    CapabilityContract(
        capability_code=CapabilityCode.PRODUCT_DISCOVERY,
        display_name="상품 발굴",
        display_name_en="Product Discovery",
        capability_type=CapabilityType.EXTERNAL_DATA_PROVIDER,
        purpose=(
            "네이버 검색어 트렌드 공식 API(NAVER API HUB, 자격증명 등록 "
            "시)로 실제 검색 트렌드 신호를 조회하거나, 자격증명 미등록/"
            "테스트 환경에서는 Fixture 데이터로 트렌드 상품 후보를 "
            "발굴한다."
        ),
        responsibilities=(
            "승인된 자료 출처에서 후보 발견",
            "후보 식별정보·출처·확인일 기록",
            "중복 후보 확인",
        ),
        allowed_operations=(
            "네이버 검색어 트렌드 공식 API 호출(NAVER API HUB — "
            "naverapihub.apigw.ntruss.com/search-trend/v1/search, "
            "2026-09-07부로 구 openapi.naver.com/v1/datalab/search "
            "대체)",
            "후보 조회", "점수 표시",
        ),
        forbidden_operations=(
            "네이버 검색어 트렌드 API 외 비공식/무단 수집(스크래핑 등)",
            "후보 자동 승인", "판매 가능 확정", "출처 없는 수요 수치 생성",
        ),
        required_inputs=("시장 코드", "기간"),
        allowed_evidence_sources=(
            "네이버 검색어 트렌드 공식 API 응답(NaverDataLabTrendAdapter, "
            "NAVER API HUB)",
            "app/domains/trend_discovery/fixtures 내 고정 데이터(테스트 전용)",
        ),
        allowed_basis=(
            "네이버 검색어 트렌드 공식 API 응답 또는 Fixture 데이터"
            "(app/domains/trend_discovery/fixtures, 테스트 전용)"
        ),
        decision_criteria="실제 검색량 시계열(또는 Fixture 값) — 점수화 자체는 PRODUCT_ANALYSIS(RULE_ENGINE)가 별도로 수행",
        missing_data_handling=(
            "API 응답 없음/자격증명 미등록/네트워크 실패는 전부 None "
            "반환(추정 금지, fail-closed) — Fixture에 없는 조합도 동일. "
            "무료 할당량(월 30,000회) 도달 시에도 호출 자체를 하지 "
            "않고 동일하게 None을 반환한다(유료 전환 없음)."
        ),
        output_contract=("AI_ESTIMATE", "EVIDENCE_REQUIRED"),
        required_permission=None,
        max_automation_level=AutomationLevel.L1_ANALYZE_RECOMMEND,
        user_approval_condition="운영자가 후보를 검토 후 별도 승인 액션을 눌러야 함",
        execution_limit=None,
        stop_condition="EStop 활성화 시 신규 조회 화면 진입만 차단(SafetyService)",
        external_provider="네이버 검색어 트렌드(NAVER API HUB — NAVER Cloud Platform 콘솔 발급, 구 developers.naver.com 오픈API는 2026-07-31부로 신규 발급 종료)",
        model_prompt_policy_version="naver-datalab-v1",
        provider_status=(
            "2026-09-07 사용자 지시로 3단계 구분 — "
            "① 코드 존재: TRUE(2026-09-06 최초 구현). "
            "② API HUB 대응 완료: TRUE(2026-09-07 — 엔드포인트를 "
            "naverapihub.apigw.ntruss.com/search-trend/v1/search로, "
            "인증 헤더를 X-NCP-APIGW-API-KEY-ID/X-NCP-APIGW-API-KEY로 "
            "전면 재작성. 요청/응답 구조는 공식 문서 기준으로 재검증. "
            "격리 테스트 tests/test_naver_datalab_trend_adapter.py "
            "11건 통과, 실제 네트워크 호출 없음). "
            "③ 실제 호출 검증 완료: TRUE(2026-09-07 — 사용자가 등록한 "
            "실제 Client ID/Secret으로 NaverDataLabTrendAdapter.fetch() "
            "를 통해 키워드 '무선청소기' 실 호출 성공, HTTP 200, "
            "source=naver_api_hub_search_trend, 13주 시계열(ratio 값 "
            "포함) 정상 수신 확인. 콘솔 UI의 '실제 트렌드 데이터로 "
            "재분석' 버튼도 실제로 호출까지 도달함을 확인(다만 그 "
            "테스트 후보의 상품명 자체가 무의미한 문자열이라 네이버 "
            "쪽에 실제 검색 데이터가 없어 그 특정 호출은 NO_SIGNAL — "
            "이는 결함이 아니라 fail-closed가 의도대로 동작한 것, "
            "동일 키워드로 raw HTTP 호출도 data:[]로 동일하게 재현해 "
            "원인 확정). 사용량 카운터도 실제 호출마다 정상 증가 확인."
        ),
        actual_entry_points=(
            "app/domains/trend_discovery/adapter.py::"
            "NaverDataLabTrendAdapter.fetch() (실 Provider, 자격증명 "
            "필요)",
            "app/domains/product_candidate/trend_analysis_refresh_"
            "service.py::TrendAnalysisRefreshService.refresh() "
            "(ProductCandidate 연결 지점)",
            "app/domains/trend_discovery/adapter.py::"
            "FixtureTrendAdapter.fetch() (테스트 전용)",
        ),
        unimplemented_dependencies=(
            "사용자의 NAVER Cloud Platform API HUB 콘솔 발급 Client "
            "ID/Secret 실등록 및 최소 1회 실 호출 검증(등록 화면은 "
            "구현 완료: POST /product-candidates/system/"
            "naver-datalab-credential — Client ID/Secret은 이미 "
            "발급 완료됐으나 2026-09-07 현재 아직 등록되지 않음)",
        ),
        implementation_reference=(
            "2026-09-07 후속 갱신 — 사용자가 실제로 발급받은 키가 "
            "NAVER API HUB(NCP 콘솔) 방식임이 확인되어, 2026-09-06 "
            "구현 당시 사용한 openapi.naver.com/X-Naver-Client-Id 방식"
            "(구 개발자센터 스펙)에서 naverapihub.apigw.ntruss.com/"
            "X-NCP-APIGW-API-KEY-ID 방식으로 엔드포인트·인증 헤더를"
            "전면 재작성했다(공식 문서 api.ncloud-docs.com/docs/"
            "naver-api-hub-search-trend 기준). 무료 할당량(월 30,000회)"
            "은 app/core/api_usage_tracker.py::ApiUsageTracker로 "
            "파일 기반 강제(DB Migration 없음, 별도 승인 불필요 범위)."
            "\n\n"
            "2026-09-07 갱신 — Gate AI-F6(2026-08-22) 당시 판단(아래 "
            "원문 보존)은 '값이 Fixture뿐이라 실 기능으로 노출하면 "
            "과장'이라는 전제였다. 그 전제가 2026-09-06 "
            "NaverDataLabTrendAdapter 구현으로 해소되어(실제 공식 API "
            "응답을 사용하므로 더 이상 과장이 아님) forbidden_"
            "operations의 '실제 외부 트렌드 API 호출' 항목을 정식으로 "
            "해제한다(사용자 승인, 2026-09-07). HTTP Router를 "
            "trend_discovery 자체에 신설하지 않는다는 부분은 그대로 "
            "유지한다 — 실제 진입점은 product_candidate 라우터의 "
            "refresh-trend-analysis를 통해서만 노출된다(원래 "
            "candidate_pipeline_service.py 경로와 동일한 '기존 승인 "
            "게이트를 통해서만 도달' 원칙).\n\n"
            "[2026-08-22 원문 보존] app/domains/trend_discovery/"
            "adapter.py::FixtureTrendAdapter — Gate AI-F6 판단: "
            "internal-only 유지(HTTP Router 신설하지 않음). 이유: 값이 "
            "Fixture(고정 시연 데이터)일 뿐 실제 시장 신호가 아니라, "
            "사용자가 클릭할 수 있는 '실제 기능'으로 노출하면 실제 "
            "트렌드 조회로 오인시킬 위험이 크다(과장 보고 금지 원칙과 "
            "충돌). 호출 가능한 실제 파이프라인 자체는 tests/test_"
            "trend_discovery.py(14건)로 증명됨."
        ),
    ),
    CapabilityContract(
        capability_code=CapabilityCode.PRODUCT_ANALYSIS,
        display_name="상품 분석",
        display_name_en="Product Analysis",
        capability_type=CapabilityType.RULE_ENGINE,
        purpose="후보 상품의 트렌드·신제품 여부를 결정론적 가중치 규칙으로 점수화한다.",
        responsibilities=(
            "트렌드 성장률·경쟁도 점수화",
            "신제품 여부 판정(출시 15일 기준)",
            "분석 근거 evidence 기록",
        ),
        allowed_operations=("점수 계산", "근거 evidence 기록"),
        forbidden_operations=(
            "LLM 등 비결정론적 판단으로 핵심 점수 산출", "점수 임의 보정",
            "판매 가능 확정", "사용자 승인 대체",
        ),
        required_inputs=("후보 상품 데이터", "가중치 설정"),
        allowed_evidence_sources=(
            "TrendSignal(Fixture)", "release_date(사용자 입력)",
        ),
        allowed_basis="명시적으로 분리된 규칙·가중치(코드에 고정)",
        decision_criteria="가중치 합산 — 동일 입력은 항상 동일 점수(결정론적)",
        missing_data_handling="핵심 입력 누락 시 점수 계산 자체를 차단(0으로 대체하지 않음)",
        output_contract=("CALCULATED_RESULT", "AI_ESTIMATE"),
        required_permission=None,
        max_automation_level=AutomationLevel.L1_ANALYZE_RECOMMEND,
        user_approval_condition="점수는 참고용 — 실제 승인은 운영자의 별도 결정",
        execution_limit=None,
        stop_condition="EStop 활성화 시 신규 평가 차단",
        external_provider=None,
        model_prompt_policy_version="deterministic-v1",
        provider_status="NO_REAL_PROVIDER — 결정론적 규칙 엔진만, LLM/외부 AI 없음",
        actual_entry_points=(
            "app/domains/trend_discovery/service.py::"
            "TrendDiscoveryService.evaluate()",
            "app/domains/new_product_discovery/service.py::"
            "NewProductDiscoveryService.evaluate()",
        ),
        unimplemented_dependencies=(),
        implementation_reference=(
            "app/domains/trend_discovery/service.py::TrendDiscoveryService.evaluate / "
            "app/domains/new_product_discovery/service.py::"
            "NewProductDiscoveryService.evaluate — Gate AI-F6(2026-08-22) "
            "판단: PRODUCT_DISCOVERY와 동일 이유로 internal-only 유지. "
            "신제품 판정(15일 기준)은 결정론적이라 실제 값이지만, "
            "트렌드 성장률 축은 PRODUCT_DISCOVERY의 Fixture 신호에 "
            "의존해 그 한계를 그대로 물려받는다. 호출 가능한 실제 "
            "파이프라인은 tests/test_trend_discovery.py + tests/"
            "test_new_product_discovery.py(총 24건)로 증명됨."
        ),
    ),
    CapabilityContract(
        capability_code=CapabilityCode.PRODUCT_SELECTION,
        display_name="상품 선별(Decision AI)",
        display_name_en="Product Selection (Decision AI)",
        capability_type=CapabilityType.RULE_ENGINE,
        purpose="후보 상품을 평가·추천한다 — 최종 승인/보류/거절은 운영자 결정.",
        responsibilities=(
            "정책 통과 후보의 축별 점수화",
            "추천/보류/제외 근거 생성",
            "9단계 선별 현황(정책→권리→공급→운영→수익성→위험→승인) 종합 표시",
        ),
        allowed_operations=("평가", "추천 점수 산출", "근거 기록"),
        forbidden_operations=(
            "자동 상태 전이(승인/보류/거절)", "실제 LLM 호출 자칭",
            "정책 차단 우회", "인증·권리 누락 무시",
        ),
        required_inputs=("후보 상품 데이터", "평가 축별 입력"),
        allowed_evidence_sources=(
            "운영자 제공 평가 축 입력", "channel_policy 평가 결과",
            "media_asset.rights_status", "source 공급처 연결",
        ),
        allowed_basis='evaluator_kind="deterministic" — 가중치 기반 규칙 엔진',
        decision_criteria=(
            "판정 순서: 정책 → 권리 → 공급 → 운영 → 수익성 → 위험 → "
            "사용자 승인. 축별 가중치 합산, 고정 임계값."
        ),
        missing_data_handling="축별 입력 누락 시 해당 축은 평가 불가로 표시(추정 금지)",
        output_contract=(
            "CALCULATED_RESULT", "EVIDENCE_REQUIRED", "HUMAN_REVIEW_REQUIRED",
        ),
        required_permission=None,
        max_automation_level=AutomationLevel.L2_DRAFT_PREPARE,
        user_approval_condition="AI가 자동으로 상태를 바꾸지 않는다 — 운영자가 직접 승인/보류/거절",
        execution_limit=None,
        stop_condition="EStop 활성화 시 신규 평가 차단",
        external_provider=None,
        model_prompt_policy_version="deterministic-v1",
        provider_status=(
            "NO_REAL_PROVIDER — evaluator_kind=deterministic만 구현, "
            "DecisionEvaluatorProtocol(실 LLM용)은 정의만 되어 있고 미연결"
        ),
        actual_entry_points=(
            "app/domains/decision/service.py::"
            "DecisionService.evaluate_candidate()",
            "app/domains/product_selection/service.py::"
            "ProductSelectionService.get_overview()",
        ),
        unimplemented_dependencies=(
            "DecisionEvaluatorProtocol 실 LLM 연동 미구현",
            "재판매 권리 도메인 자체가 아직 없음(product_selection이 "
            "항상 DATA_REQUIRED로 정직하게 표시)",
        ),
        implementation_reference=(
            "app/domains/decision/service.py::DecisionService.evaluate_candidate — "
            "실 LLM 연동용 DecisionEvaluatorProtocol은 정의만 되어 있고 미연결"
        ),
    ),
    CapabilityContract(
        capability_code=CapabilityCode.PROFITABILITY_CALCULATION,
        display_name="수익성 계산",
        display_name_en="Profitability Calculation",
        capability_type=CapabilityType.CALCULATOR,
        purpose="판매가·원가·수수료·배송비 등으로 예상 마진을 계산한다.",
        responsibilities=(
            "공헌이익·마진율·손익분기점 계산",
            "잠정/확정 구분 표시",
            "회사 목표 마진율 대비 충족 여부 판정",
        ),
        allowed_operations=("마진 계산", "손익분기 계산", "잠정/확정 구분 표시"),
        forbidden_operations=("확인되지 않은 비용을 0으로 대체", "정책 판정과 결과 혼합"),
        required_inputs=("판매가", "원가", "채널·결제 수수료", "배송·포장·광고비"),
        allowed_evidence_sources=(
            "사용자 입력 원가·수수료·배송비",
            "CompanyChannelPolicySettings.min_target_margin_rate",
        ),
        allowed_basis="순수 Decimal 계산 함수(DB·AI 의존 없음)",
        decision_criteria="고정 수식(app/domains/marketplace_listing/margin_calculator.py)",
        missing_data_handling="누락 필드는 missing_cost_fields에 명시, is_provisional=True",
        output_contract=("CALCULATED_RESULT", "EVIDENCE_REQUIRED"),
        required_permission=None,
        max_automation_level=AutomationLevel.L1_ANALYZE_RECOMMEND,
        user_approval_condition="계산 결과는 참고용 — 실제 판매가 결정은 운영자 몫",
        execution_limit=None,
        stop_condition="해당 없음(순수 계산, 부작용 없음)",
        external_provider=None,
        model_prompt_policy_version="calculator-v1",
        provider_status="NO_AI — 순수 Decimal 계산 함수(CALCULATOR), 추론 없음",
        actual_entry_points=(
            "app/domains/channel_policy/service.py::"
            "ChannelPolicyService.estimate_margin()",
            "app/domains/marketplace_listing/listing_wizard_service.py::"
            "ListingWizardService.update_economics()",
        ),
        unimplemented_dependencies=(),
        implementation_reference=(
            "app/domains/channel_policy/service.py::ChannelPolicyService."
            "estimate_margin / "
            "app/domains/marketplace_listing/listing_wizard_service.py::"
            "ListingWizardService.update_economics"
        ),
    ),
    CapabilityContract(
        capability_code=CapabilityCode.CONTENT_GENERATION,
        display_name="콘텐츠 생성",
        display_name_en="Content Generation",
        capability_type=CapabilityType.FAKE_PROVIDER,
        purpose="상품 등록용 상품 설명 초안을 생성한다.",
        responsibilities=(
            "상품 설명·검색어 초안 생성", "채널별 표현 변환",
            "금지 표현 경고 표시(콘텐츠 정책 체크리스트와 연동)",
        ),
        allowed_operations=("초안 텍스트 생성",),
        forbidden_operations=(
            "실제 LLM 호출 자칭", "생성물을 최종 등록 문구로 자동 확정",
            "허위 효능·인증·원산지 생성", "다른 상품 정보와 혼합",
        ),
        required_inputs=("상품명", "카테고리"),
        allowed_evidence_sources=("사용자 확인 상품명·카테고리·브랜드 힌트",),
        allowed_basis="결정론적 해시 기반 Fake 생성(app/domains/marketplace_listing/draft_content_provider.py)",
        decision_criteria="입력 해시 → 고정 템플릿 — 동일 입력은 항상 동일 결과",
        missing_data_handling="상품명 없으면 생성 자체를 거부",
        output_contract=("AI_ESTIMATE", "EVIDENCE_REQUIRED"),
        required_permission=None,
        max_automation_level=AutomationLevel.L2_DRAFT_PREPARE,
        user_approval_condition="운영자가 초안을 검토·수정 후 별도로 확정해야 함",
        execution_limit=None,
        stop_condition="EStop과 무관(부작용 없는 초안 생성)",
        external_provider=None,
        model_prompt_policy_version="fake-draft-v1",
        provider_status=(
            "NO_REAL_PROVIDER — FakeDraftContentProvider(결정론적 해시 "
            "템플릿)만 존재, 실 LLM 미연동. DisabledDraftContentProvider가 "
            "fail-closed 기본값."
        ),
        actual_entry_points=(
            "app/domains/marketplace_listing/candidate_pipeline_service.py::"
            "CandidatePipelineService.start_pipeline()",
        ),
        unimplemented_dependencies=(
            "실 LLM Provider 없음",
            "허위 효능/인증/원산지 방지는 run_content_policy_checklist()"
            "가 별도 담당 — 이 capability 자체는 문구 생성만 수행",
        ),
        implementation_reference=(
            "app/domains/marketplace_listing/draft_content_provider.py::"
            "FakeDraftContentProvider / DisabledDraftContentProvider(fail-closed)"
        ),
    ),
    CapabilityContract(
        capability_code=CapabilityCode.IMAGE_PROCESSING,
        display_name="이미지 처리",
        display_name_en="Image Processing",
        capability_type=CapabilityType.FAKE_PROVIDER,
        purpose="상품 이미지를 생성·가공한다(대표/상세 이미지).",
        responsibilities=(
            "원본 이미지 가공(누끼·배경·크기조정)",
            "파생 이미지 계보 기록", "채널 규격 변환",
        ),
        allowed_operations=("Fake 이미지 생성", "배경 제거 시연"),
        forbidden_operations=(
            "실제 유료 이미지 생성 API 호출",
            "권리 미확인 이미지의 자동 VERIFIED 전환",
            "다른 상품·향·용량 혼합", "라벨·인증마크·주의문구 조작",
            "결과를 실제 촬영 이미지로 위장",
        ),
        required_inputs=("상품명 또는 원본 이미지",),
        allowed_evidence_sources=(
            "source_asset_id(사용자 업로드 원본)", "rights_status(사용자 확인)",
        ),
        allowed_basis="결정론적 해시 기반 Fake PNG 생성(app/domains/media_asset/providers.py)",
        decision_criteria="입력 해시 → 고정 이미지 — 실제 이미지 생성 로직 없음",
        missing_data_handling="입력 없으면 생성 거부",
        output_contract=("AI_ESTIMATE", "EVIDENCE_REQUIRED"),
        required_permission=None,
        max_automation_level=AutomationLevel.L2_DRAFT_PREPARE,
        user_approval_condition="rights_status는 사용자의 명시적 확인 후에만 VERIFIED 전환",
        execution_limit=None,
        stop_condition="EStop과 무관",
        external_provider=None,
        model_prompt_policy_version="fake-image-v1",
        provider_status=(
            "NO_REAL_PROVIDER — FakeImageGenerationProvider(결정론적 해시 "
            "PNG)만 존재. DisabledImageGenerationProvider가 fail-closed "
            "기본값. (2026-09-07 명시 — 이 상태는 '이미지 생성'에만 "
            "해당한다. app/domains/media_asset/image_search_"
            "providers.py::NaverImageSearchProvider는 별개 기능인 "
            "'이미지 검색'(기존 인터넷 이미지에서 후보를 찾아 미리보기로 "
            "보여줌)이며 이 capability와 무관하다 — 이미지 검색 연결을 "
            "이미지 생성 AI 완성으로 오인 보고하지 않는다는 원칙에 "
            "따른 명시적 구분. NaverImageSearchProvider 자체의 3단계 "
            "상태: ①코드 존재 TRUE(2026-09-07) ②API HUB 대응 완료 "
            "TRUE(2026-09-07, GET naverapihub.apigw.ntruss.com/"
            "search/v1/image, 격리 테스트 tests/test_naver_image_"
            "search_provider.py 9건 통과) ③실제 호출 검증 완료 "
            "TRUE(2026-09-07, 사용자의 실제 키로 검색어 '무선청소기' "
            "실 호출 성공 — HTTP 200, 결과 20건, 실제 네이버 CDN "
            "썸네일 URL 수신 확인, permission_status는 예상대로 항상 "
            "UNKNOWN·selectable=false로 응답됨). 다만 이 결과 전부는 "
            "여전히 상업적 재사용 허가가 확인된 이미지가 아니다 — "
            "운영자가 출처 페이지를 직접 확인해야 한다.)"
        ),
        actual_entry_points=(
            "app/domains/media_asset/job_queue_service.py::"
            "ImageGenerationJobQueueService.submit_job()",
        ),
        unimplemented_dependencies=("실제 이미지 생성 API 없음",),
        implementation_reference=(
            "app/domains/media_asset/providers.py::FakeImageGenerationProvider / "
            "DisabledImageGenerationProvider(실제 Provider 미연결 시 fail-closed)"
        ),
    ),
    CapabilityContract(
        capability_code=CapabilityCode.CHANNEL_POLICY_ASSIST,
        display_name="판매채널 정책",
        display_name_en="Channel Policy Assist",
        capability_type=CapabilityType.RULE_ENGINE,
        purpose="판매채널(쿠팡·네이버) 정책 적합성을 자동 판정한다.",
        responsibilities=(
            "공식 정책 규칙 대비 상품 적합성 평가",
            "위반/자료필요/통과 판정 및 근거 표시",
            "정책 카탈로그 버전 관리(profile_version)",
        ),
        allowed_operations=("정책 규칙 평가", "위반/자료필요/통과 판정"),
        forbidden_operations=(
            "공식 근거 없는 규칙 활성화", "수익성 판단과 결과 혼합",
            "정책 차단 우회", "자유 텍스트만으로 법적 카테고리 확정",
            "정책 미시딩 상태를 판매 가능으로 처리",
        ),
        required_inputs=("상품 후보", "채널 코드", "구조화 상품 속성"),
        allowed_evidence_sources=(
            "marketplace.coupang.com/developers.coupang.com 공식 문서로 "
            "확인된 규칙만", "사용자 확인 구조화 상품 속성",
        ),
        allowed_basis="공식 채널 정책 문서(marketplace.coupang.com, developers.coupang.com)로 확인된 규칙만",
        decision_criteria="rule_code별 카테고리/구조화필드/증빙 매칭 — 5종 결과 분류",
        missing_data_handling="필수 구조화 필드 누락 시 CHANNEL_DATA_REQUIRED(추정하지 않음)",
        output_contract=("CALCULATED_RESULT", "EVIDENCE_REQUIRED", "POLICY_BLOCKED"),
        required_permission="listing_wizard.view",
        max_automation_level=AutomationLevel.L1_ANALYZE_RECOMMEND,
        user_approval_condition="BLOCK/DATA_REQUIRED/STALE은 통과 불가 — 사용자가 보완 후 재평가해야 함",
        execution_limit=None,
        stop_condition="해당 없음(순수 평가, 부작용은 append-only 기록만)",
        external_provider=None,
        model_prompt_policy_version="rule-catalog-v1",
        provider_status="NO_AI — 순수 규칙 엔진(RULE_ENGINE), 공식 문서 기반 정적 카탈로그",
        actual_entry_points=(
            "app/domains/channel_policy/service.py::"
            "ChannelPolicyService.evaluate_and_record()",
        ),
        unimplemented_dependencies=(
            "네이버 스마트스토어 규칙 2건 공식 근거 미확보"
            "(POLICY_EVIDENCE_REQUIRED, active=False 유지)",
        ),
        implementation_reference="app/domains/channel_policy/engine.py::evaluate_policy",
    ),
    CapabilityContract(
        capability_code=CapabilityCode.SUPPLIER_RECOMMENDATION,
        display_name="공급처 추천",
        display_name_en="Supplier Recommendation",
        capability_type=CapabilityType.FAKE_PROVIDER,
        purpose="상품과 매칭될 공급처를 검색·추천한다.",
        responsibilities=(
            "공급가·MOQ·재고·리드타임 비교", "공급처 후보 검색 결과 표시",
        ),
        allowed_operations=("Fake/CSV/수동 검색 결과 표시",),
        forbidden_operations=(
            "실제 인터넷/B2B 공급처 API 호출", "검색 결과의 자동 연결 확정",
            "공급처 자동 승인·계약·결제·실제 발주", "Credential 원문 처리",
        ),
        required_inputs=("상품명 또는 CSV 행",),
        allowed_evidence_sources=(
            "Fixture(FAKE)", "사용자 업로드 CSV", "수동 입력(MANUAL)",
        ),
        allowed_basis="Fixture(FAKE)/사용자 업로드(CSV)/수동 입력(MANUAL)만",
        decision_criteria="Fake는 고정 시연 데이터, CSV는 사용자 입력 행 그대로 표시",
        missing_data_handling="CSV 행이 없으면 빈 결과, 추정 데이터 생성 없음",
        output_contract=("AI_ESTIMATE", "CONFIRMED_DATA"),
        required_permission="SUPPLIER_VIEW",
        max_automation_level=AutomationLevel.L1_ANALYZE_RECOMMEND,
        user_approval_condition="연결 확정은 운영자의 별도 액션 필요",
        execution_limit=None,
        stop_condition="해당 없음",
        external_provider=None,
        model_prompt_policy_version="fake-supplier-v1",
        provider_status="NO_REAL_PROVIDER — Fake/CSV/Manual 3종만, 실제 B2B 공급처 API 없음",
        actual_entry_points=(
            "app/domains/source/router.py::search_suppliers()",
        ),
        unimplemented_dependencies=("실제 공급처 검색 API 없음",),
        implementation_reference=(
            "app/domains/source/discovery_providers.py::FakeSupplierDiscoveryProvider / "
            "ManualSupplierDiscoveryProvider / CsvSupplierDiscoveryProvider"
        ),
    ),
    CapabilityContract(
        capability_code=CapabilityCode.PRICING_INVENTORY,
        display_name="가격·재고 제안",
        display_name_en="Pricing & Inventory Advisory",
        capability_type=CapabilityType.CALCULATOR,
        purpose=(
            "목표 마진을 고려한 가격 제안과 재고 부족 예측·발주 필요 "
            "시점을 제안한다(Gate AI-F1, 2026-08-22 — 실제 계산 로직 "
            "구현 완료). 실제 재고 예약/해제/소진/재입고 트랜잭션과 "
            "실제 가격 변경 요청 생성은 운영자가 직접 수행하는 핵심 "
            "업무 CRUD이며(AI 판단 대상이 아님) 이 capability로 게이트"
            "하지 않는다(AG-0, 2026-08-21 — 이전 라운드에서 여기에 잘못 "
            "연결했던 것을 되돌림)."
        ),
        responsibilities=(
            "가격 제안 계산(margin_calculator.calculate_economics 재사용, "
            "목표 마진 역산 후 동일 공식으로 재검증)",
            "안전재고·예상 품절일·권장 발주 시점·수량 계산"
            "(CONSUMED 이력 기반 판매 속도, MOQ 반영)",
            "REVIEW_REQUIRED ProposedAction 생성(실행 가능한 제안일 때만)",
        ),
        allowed_operations=(
            "가격안 계산", "손익분기가·잠정 마진 표시",
            "안전재고·발주 시점·수량 제안", "MOQ 반영",
        ),
        forbidden_operations=(
            "실제 가격 변경 직접 실행", "실제 재고 수량 조작",
            "재고 잠금 우회", "자동 발주",
            "누락 비용을 0으로 대체", "정책상 판매 불가 상품 가격 제안",
            "AI가 ProposedAction을 APPROVED/EXECUTED로 직접 전이",
        ),
        required_inputs=(
            "현재 판매가", "매입원가", "채널·결제 수수료", "배송·포장·광고비",
            "반품 충당금율", "부가세 기준율", "회사 목표 마진율",
            "현재고", "예약재고", "안전재고", "최근 판매(CONSUMED) 이력",
            "공급처 MOQ·리드타임",
        ),
        allowed_evidence_sources=(
            "사용자 입력 원가·수수료 등 비용 항목",
            "InventoryLedgerEvent(CONSUMED) 실제 판매 이력",
            "SupplierProductLink.moq/lead_time_days",
            "ChannelPolicyService 현재 상태(정책 차단 여부 확인용)",
        ),
        allowed_basis=(
            "가격: margin_calculator.calculate_economics()(기존 공식 "
            "재사용, 새 공식 없음). 재고: 최근 N일 CONSUMED 합계 기반 "
            "일평균 판매 속도 — AI/LLM 추론 없음."
        ),
        decision_criteria=(
            "가격 = fixed_costs / ((1-rate_sum) - target_margin_rate), "
            "불가능하면(분모<=0) 계산하지 않음. 재고 = 안전재고 + "
            "avg_daily_sales*lead_time_days를 트리거 기준으로 사용, "
            "MOQ 미만이면 MOQ로 상향."
        ),
        missing_data_handling=(
            "가격: 누락 비용 필드는 missing_cost_fields에 명시, "
            "is_provisional=True(0 대체 없음). 재고: 판매 이력 0건이면 "
            "수요를 추정하지 않고 missing_evidence로 표시."
        ),
        output_contract=("CALCULATED_RESULT", "AI_ESTIMATE", "EVIDENCE_REQUIRED", "POLICY_BLOCKED"),
        required_permission=None,
        max_automation_level=AutomationLevel.L2_DRAFT_PREPARE,
        user_approval_condition=(
            "제안 자체는 ProposedAction(REVIEW_REQUIRED)일 뿐 실행이 "
            "아니다 — 사람이 승인해야 하고, 가격 변경의 실제 반영은 "
            "그 승인 이후 기존 approve_price_change()가 별도로 수행. "
            "재고 조작·발주 실행도 운영자가 직접 수행(이 capability와 "
            "무관)."
        ),
        execution_limit=None,
        stop_condition="해당 없음(제안 계산 자체는 부작용 없는 조회, ProposedAction 생성은 append 전용)",
        external_provider=None,
        model_prompt_policy_version="calculator-v1",
        provider_status=(
            "NO_AI — 순수 계산(CALCULATOR): 가격은 기존 margin_"
            "calculator 공식 역산+재검증, 재고는 실제 판매 이력 집계. "
            "실제 재고 트랜잭션(inventory.reserve/release/consume/"
            "restock)과 가격 변경 요청(pricing.request_price_change)은 "
            "여전히 사람이 직접 수행하는 별도 CRUD로, 이 capability와 "
            "연결되어 있지 않다(AG-0에서 되돌린 상태 유지 — 제안만 "
            "이 capability, 실행은 항상 별도)."
        ),
        actual_entry_points=(
            "app/domains/pricing/price_advisory_service.py::"
            "PriceAdvisoryService.suggest() / propose_price_change()",
            "app/domains/inventory/replenishment_advisory_service.py::"
            "ReplenishmentAdvisoryService.suggest() / "
            "propose_replenishment()",
        ),
        unimplemented_dependencies=(
            "실 HTTP 진입점 없음(Service 레벨까지만 구현, Router 미연결)",
            "AI 업무 제안 UI 미연결(AG-5, 2026-08-21 라운드부터 이월)",
        ),
        implementation_reference=(
            "app/domains/pricing/price_advisory_service.py::"
            "PriceAdvisoryService / app/domains/inventory/"
            "replenishment_advisory_service.py::"
            "ReplenishmentAdvisoryService — 실제 재고·가격 CRUD는 "
            "app/domains/inventory/service.py::InventoryService, "
            "app/domains/pricing/service.py::PricingService가 이 "
            "capability와 무관하게 직접 담당"
        ),
    ),
    CapabilityContract(
        capability_code=CapabilityCode.ORDER_SHIPMENT_RETURN,
        display_name="주문·배송·반품 처리안",
        display_name_en="Order/Shipment/Return Advisory",
        capability_type=CapabilityType.RULE_ENGINE,
        purpose=(
            "주문·배송·반품의 예외·지연을 분석하고 처리 우선순위·"
            "처리안을 제안한다(Gate AI-F2, 2026-08-22 — 실제 분석 "
            "로직 구현 완료). 실제 주문 수집·배송 생성·반품 생성과 "
            "모든 상태 전이는 운영자/시스템이 직접 수행하는 핵심 업무 "
            "CRUD이며 이 capability로 게이트하지 않는다(AG-0, "
            "2026-08-21 — 이전 라운드에서 여기에 잘못 연결했던 것을 "
            "되돌림)."
        ),
        responsibilities=(
            "처리 대기·재고 부족·수집 실패·배송 준비 지연·송장 누락·"
            "배송 지연·반품 요청·반품 처리 지연·환불/재배송 검토 "
            "필요 탐지(9종)",
            "긴급도(HIGH/MEDIUM) 분류 및 실제 상태 근거·경과 시간 표시",
            "REVIEW_REQUIRED ProposedAction 생성(실행 가능할 때만)",
        ),
        allowed_operations=("예외·지연 분석", "긴급도 분류", "권장 조치 표시"),
        forbidden_operations=(
            "주문 취소", "배송 상태 임의 변경", "반품 승인", "환불",
            "재배송", "상태 전이 직접 실행",
            "AI가 ProposedAction을 APPROVED/EXECUTED로 직접 전이",
        ),
        required_inputs=(
            "Order.status/created_at", "OrderItem.status",
            "OrderIngestionEvent.status", "Shipment.status/invoice_number",
            "ReturnOrder.status/created_at", "EStop 상태",
        ),
        allowed_evidence_sources=(
            "실제 Order/OrderItem/OrderIngestionEvent/Shipment/"
            "ReturnOrder 테이블 조회(전부 사실 — 추정 없음)",
        ),
        allowed_basis=(
            "AI/추론 없음 — 상태값·경과시간 임계값 비교(정의된 상태 "
            "머신 전이 규칙과는 별개의 읽기 전용 임계값 판정). "
            "임계값(PENDING/READY/IN_TRANSIT/반품 검토 지연 기준 "
            "시간)은 공식 SLA가 아니라 운영 휴리스틱 기본값 — "
            "정직하게 assumptions에 명시."
        ),
        decision_criteria=(
            "상태값 일치 + 경과시간 >= 임계값(app/domains/order/"
            "exception_analysis_service.py 상단 상수) — 동일 입력은 "
            "항상 동일 판정."
        ),
        missing_data_handling="송장번호처럼 값 자체가 없으면 missing_evidence에 명시(0/빈값으로 대체하지 않음)",
        output_contract=("HUMAN_REVIEW_REQUIRED", "CONFIRMED_DATA"),
        required_permission=None,
        max_automation_level=AutomationLevel.L2_DRAFT_PREPARE,
        user_approval_condition=(
            "분석 결과의 execution_allowed는 EStop 상태를 그대로 "
            "반영한다(EStop 활성 중에는 탐지는 계속하되 실행 가능 "
            "표시는 False, ProposedAction도 만들지 않음). 실제 상태 "
            "전이는 운영자가 직접 수행."
        ),
        execution_limit=None,
        stop_condition="EStop 활성화 시 탐지된 예외의 execution_allowed=False, 신규 ProposedAction 생성 차단",
        external_provider=None,
        model_prompt_policy_version="deterministic-v1",
        provider_status=(
            "NO_AI — 순수 규칙 기반 임계값 판정(RULE_ENGINE). 실제 "
            "주문/배송/반품 생성과 상태 전이(order.collect_channel_"
            "order, shipment.create_shipment, return_order.create_"
            "return_order 및 각 도메인의 상태 전이 메서드)는 여전히 "
            "사람/시스템이 직접 수행하는 별도 CRUD로, 이 capability와 "
            "연결되어 있지 않다(AG-0에서 되돌린 상태 유지 — 분석만 "
            "이 capability, 실행은 항상 별도)."
        ),
        actual_entry_points=(
            "app/domains/order/exception_analysis_service.py::"
            "OrderExceptionAnalysisService.analyze() / "
            "create_review_action()",
            "GET /orders/exception-analysis, POST /orders/"
            "exception-analysis/review-actions (app/domains/order/"
            "router.py)",
        ),
        unimplemented_dependencies=(
            "\"취소 요청\" 탐지 미구현(Order.status에 별도 CANCEL_"
            "REQUESTED 상태가 없어 CANCELLED와 구분할 신호 없음)",
            "AI 업무 제안 UI 미연결",
        ),
        implementation_reference=(
            "app/domains/order/exception_analysis_service.py::"
            "OrderExceptionAnalysisService — 실제 CRUD는 app/domains/"
            "order/service.py::OrderService, app/domains/shipment/"
            "service.py::ShipmentService, app/domains/return_order/"
            "service.py::ReturnOrderService가 이 capability와 무관하게 "
            "직접 담당"
        ),
    ),
    CapabilityContract(
        capability_code=CapabilityCode.SETTLEMENT,
        display_name="정산 차이 분석",
        display_name_en="Settlement Variance Analysis",
        capability_type=CapabilityType.CALCULATOR,
        purpose=(
            "예상 정산과 실제 정산의 차이를 분석하고 확인 우선순위를 "
            "제안한다(Gate AI-F2, 2026-08-22 — 실제 분석 로직 구현 "
            "완료). 실제 입금 확정은 운영자가 직접 수행하는 핵심 재무 "
            "업무이며 이 capability로 게이트하지 않는다(AG-0, "
            "2026-08-21 — 이전 라운드에서 여기에 잘못 연결했던 것을 "
            "되돌림)."
        ),
        responsibilities=(
            "주문 총액 대비 정산 gross_amount 불일치 탐지",
            "PENDING/MISMATCH/HELD 방치 탐지",
            "REVIEW_REQUIRED ProposedAction 생성(실행 가능할 때만)",
        ),
        allowed_operations=("차이 분석", "긴급도 분류", "확인 우선순위 표시"),
        forbidden_operations=(
            "입금 확정", "회계 확정", "지급", "환불", "세금신고",
            "AI가 ProposedAction을 APPROVED/EXECUTED로 직접 전이",
        ),
        required_inputs=(
            "MarketplaceSettlement.gross_amount/status/created_at/"
            "updated_at", "Order.total_amount", "EStop 상태",
        ),
        allowed_evidence_sources=(
            "실제 MarketplaceSettlement/Order 테이블 조회(전부 사실 — "
            "채널 원본 정산 명세서 파일 대사는 아직 미연동)",
        ),
        allowed_basis=(
            "AI/추론 없음 — 금액 차이·경과시간 임계값 비교(입금 확정 "
            "CRUD 자체와는 별개의 읽기 전용 판정). 임계값은 공식 SLA가 "
            "아니라 운영 휴리스틱 기본값 — 정직하게 assumptions에 명시."
        ),
        decision_criteria=(
            "|gross_amount - Order.total_amount| > 허용오차, 또는 "
            "상태별 경과시간 >= 임계값(app/domains/settlement/"
            "difference_analysis_service.py 상단 상수) — 동일 입력은 "
            "항상 동일 판정."
        ),
        missing_data_handling="주문이 연결 안 된 정산은 금액 비교를 건너뛰고 방치 탐지만 수행(0/추정치로 대체하지 않음)",
        output_contract=("HUMAN_REVIEW_REQUIRED", "CONFIRMED_DATA"),
        required_permission=None,
        max_automation_level=AutomationLevel.L1_ANALYZE_RECOMMEND,
        user_approval_condition=(
            "분석 결과의 execution_allowed는 EStop 상태를 그대로 "
            "반영한다(EStop 활성 중에는 탐지는 계속하되 실행 가능 "
            "표시는 False, ProposedAction도 만들지 않음). "
            "SETTLEMENT_DIFFERENCE_REVIEW는 고위험 action_type이라 "
            "승인 시 recent-auth가 추가로 필요하다(HIGH_RISK_ACTION_"
            "TYPES). 실제 입금 확정/불일치 해소는 운영자가 직접 수행."
        ),
        execution_limit=None,
        stop_condition="EStop 활성화 시 탐지된 차이의 execution_allowed=False, 신규 ProposedAction 생성 차단",
        external_provider=None,
        model_prompt_policy_version="deterministic-v1",
        provider_status=(
            "NO_AI — 순수 규칙 기반 임계값 판정(RULE_ENGINE). 실제 "
            "입금 확정(settlement.confirm_deposit)과 MISMATCH/HELD "
            "상태 전이는 여전히 운영자가 직접 수행하는 별도 CRUD로, "
            "이 capability와 연결되어 있지 않다(AG-0에서 되돌린 상태 "
            "유지 — 분석만 이 capability, 실행은 항상 별도)."
        ),
        actual_entry_points=(
            "app/domains/settlement/difference_analysis_service.py::"
            "SettlementDifferenceAnalysisService.analyze() / "
            "create_review_action()",
            "GET /settlements/difference-analysis, POST /settlements/"
            "difference-analysis/review-actions (app/domains/"
            "settlement/router.py)",
        ),
        unimplemented_dependencies=(
            "채널이 보낸 원본 정산 명세서 파일 파싱·대사 미구현 — "
            "Order.total_amount와의 내부 비교만 수행",
            "AI 업무 제안 UI 미연결",
        ),
        implementation_reference=(
            "app/domains/settlement/difference_analysis_service.py::"
            "SettlementDifferenceAnalysisService — 실제 CRUD는 "
            "app/domains/settlement/service.py::SettlementService가 "
            "이 capability와 무관하게 직접 담당"
        ),
    ),
    CapabilityContract(
        capability_code=CapabilityCode.OPERATIONS_COORDINATION,
        display_name="운영 우선순위 제안",
        display_name_en="Operations Coordination Advisory",
        capability_type=CapabilityType.CALCULATOR,
        purpose=(
            "여러 도메인의 실제 상태를 읽어 다음 업무 우선순위를 "
            "제안한다(Gate AI-F2, 2026-08-22 — 실제 집계 로직 구현 "
            "완료). 단순 KPI 집계 조회(Dashboard)는 사람이 언제든 볼 "
            "수 있어야 하는 화면이며 이 capability로 게이트하지 "
            "않는다(AG-0, 2026-08-21 — 이전 라운드에서 여기에 잘못 "
            "연결했던 것을 되돌림)."
        ),
        responsibilities=(
            "판매 판단 대기·발주 승인 대기·발주 실패·반품 환불 대기 "
            "COUNT 집계",
            "주문·배송·반품 예외 분석(ORDER_SHIPMENT_RETURN)·정산 "
            "차이 분석(SETTLEMENT) 결과 재사용 집계",
            "긴급도 기준 우선순위 정렬",
        ),
        allowed_operations=("여러 도메인 상태 집계·긴급도 정렬",),
        forbidden_operations=(
            "원본 도메인 상태 변경", "실패를 성공으로 변경",
            "작업 자동 완료", "EStop 우회", "ProposedAction 생성",
        ),
        required_inputs=("각 도메인의 실제 상태", "사용자 Permission", "EStop", "기준 시각"),
        allowed_evidence_sources=(
            "실제 ProductCandidate/Purchase/ReturnOrder 테이블 COUNT + "
            "OrderExceptionAnalysisService/SettlementDifference"
            "AnalysisService의 실제 분석 결과 재사용(새 판정 로직 "
            "발명 없음)",
        ),
        allowed_basis=(
            "AI 없음 — 실제 테이블 직접 COUNT + 이미 구현된 하위 "
            "분석 결과 재사용·정렬만 수행"
        ),
        decision_criteria=(
            "각 항목 count > 0이면 포함, 하위 분석의 HIGH 긴급 건수가 "
            "있으면 그 항목의 urgency도 HIGH로 승격 — 새 임계값을 "
            "추가로 발명하지 않는다."
        ),
        missing_data_handling=(
            "하위 capability(ORDER_SHIPMENT_RETURN/SETTLEMENT)가 개별"
            "비활성화된 경우 해당 항목만 '집계 불가'로 missing_evidence"
            "에 명시하고 전체 조회 자체는 차단하지 않는다(AG-0 원칙 — "
            "단순 조회는 다른 capability 비활성으로 막히지 않는다). "
            "연동 안 된 지표는 0으로 위장하지 않는다."
        ),
        output_contract=("HUMAN_REVIEW_REQUIRED", "CONFIRMED_DATA"),
        required_permission=None,
        max_automation_level=AutomationLevel.L2_DRAFT_PREPARE,
        user_approval_condition=(
            "이 capability 자신은 ProposedAction을 만들지 않는다 — "
            "실제 처리 제안은 이미 하위 분석 서비스(ORDER_SHIPMENT_"
            "RETURN/SETTLEMENT)가 담당하므로 중복 생성하지 않는다."
        ),
        execution_limit=None,
        stop_condition="해당 없음(읽기 전용 집계, 실행 자체가 없음)",
        external_provider=None,
        model_prompt_policy_version="deterministic-v1",
        provider_status=(
            "NO_AI — 순수 COUNT + 하위 분석 결과 재사용·정렬"
            "(RULE_ENGINE). 단순 Dashboard 조회(orchestration."
            "dashboard_service.get_summary)는 여전히 사람이 언제든 볼 "
            "수 있는 화면으로, 이 capability와 연결되어 있지 않다"
            "(AG-0에서 되돌린 상태 유지 — 완전히 별개의 화면·경로)."
        ),
        actual_entry_points=(
            "app/domains/orchestration/priority_service.py::"
            "OperationsPriorityService.analyze()",
            "GET /orchestration/priorities (app/domains/orchestration/"
            "router.py)",
        ),
        unimplemented_dependencies=(
            "AI 업무 제안 UI 미연결",
            "상품 선별(product discovery)·수익성 계산 관련 지표는 "
            "아직 이 집계에 포함되지 않음",
        ),
        implementation_reference=(
            "app/domains/orchestration/priority_service.py::"
            "OperationsPriorityService — 단순 조회는 app/domains/"
            "orchestration/dashboard_service.py::DashboardService가 "
            "이 capability와 무관하게 직접 담당"
        ),
    ),
    CapabilityContract(
        capability_code=CapabilityCode.USER_GUIDANCE,
        display_name="대화형 사용자 안내",
        display_name_en="Interactive User Guidance",
        capability_type=CapabilityType.RULE_ENGINE,
        purpose=(
            "질문 문자열에 맞는 가이드를 추천한다(Gate AI-F2, "
            "2026-08-22 — 정적 키워드 매칭 기반 구현 완료. 화면·권한 "
            "컨텍스트 인지 대화형 안내는 아직 아님). 정적 가이드 "
            "문서·영상 목록 열람 자체(guides.list_guides)는 파일시스템 "
            "서빙일 뿐이며 이 capability로 게이트하지 않는다(AG-0, "
            "2026-08-21 — 이전 라운드에서 여기에 잘못 연결했던 것을 "
            "되돌림)."
        ),
        responsibilities=(
            "질문 문자열 ↔ 정적 키워드 사전 매칭",
            "일치하는 가이드만(파일시스템 존재 재검증 완료) 추천",
            "일치 실패 시 정직하게 EVIDENCE_REQUIRED로 응답",
        ),
        allowed_operations=("키워드 기반 가이드 매칭·추천",),
        forbidden_operations=(
            "비밀번호 요청", "Credential 요청", "사용자 대신 입력",
            "권한 없는 기능 안내", "실제 데이터 변경",
            "존재하지 않는 화면/버튼 안내",
        ),
        required_inputs=("사용자 질문 문자열", "locale"),
        allowed_evidence_sources=(
            "app/domains/guides/constants.py::GUIDE_REGISTRY + 실제 "
            "파일시스템 존재 확인(list_guides()) — 새 콘텐츠를 "
            "생성하지 않는다",
        ),
        allowed_basis=(
            "AI/생성형 모델 없음 — 정적 키워드 사전(app/domains/guides/"
            "interactive_guidance_service.py::_GUIDE_KEYWORDS) 문자열 "
            "포함 여부 매칭만 수행"
        ),
        decision_criteria=(
            "질문 문자열에 사전 키워드가 하나 이상 포함되면 해당 "
            "가이드를 점수순으로 추천, 없으면 NO_MATCH — 동일 입력은 "
            "항상 동일 결과."
        ),
        missing_data_handling="일치하는 키워드가 없으면 빈 목록 + missing_evidence에 명시(추측으로 아무 가이드나 채우지 않음)",
        output_contract=("CALCULATED_RESULT", "EVIDENCE_REQUIRED"),
        required_permission=None,
        max_automation_level=AutomationLevel.L1_ANALYZE_RECOMMEND,
        user_approval_condition="해당 없음 — 안내는 정보 제공일 뿐 실행이 아니므로 승인 대상 자체가 없다(execution_allowed 항상 False)",
        execution_limit=None,
        stop_condition="해당 없음(읽기 전용 안내, 실행 자체가 없음)",
        external_provider=None,
        model_prompt_policy_version="deterministic-v1",
        provider_status=(
            "NO_AI — 순수 정적 키워드 사전 매칭(RULE_ENGINE), 생성형 "
            "모델을 호출하지 않는다. 정적 가이드 목록 열람(guides.list_"
            "guides) 자체는 여전히 파일시스템 서빙으로, 이 capability와 "
            "연결되어 있지 않다(AG-0에서 되돌린 상태 유지 — 이 "
            "capability는 그 위에서 매칭만 추가한다)."
        ),
        actual_entry_points=(
            "app/domains/guides/interactive_guidance_service.py::"
            "InteractiveGuidanceService.ask()",
            "GET /guides/ask (app/domains/guides/router.py)",
        ),
        unimplemented_dependencies=(
            "현재 화면·사용자 역할·활성 기능 등 컨텍스트 인지 안내 "
            "미구현 — 질문 문자열만으로 매칭",
            "AI 업무 제안 UI 미연결",
        ),
        implementation_reference=(
            "app/domains/guides/interactive_guidance_service.py::"
            "InteractiveGuidanceService — 정적 가이드 서빙 자체는 "
            "app/domains/guides/service.py가 이 capability와 무관하게 "
            "직접 담당"
        ),
    ),
]


def get_capability_codes() -> list[str]:

    return [c.capability_code for c in AI_CAPABILITY_CATALOG]


__all__ = [
    "CAPABILITY_CATALOG_VERSION",
    "CapabilityContract",
    "AI_CAPABILITY_CATALOG",
    "get_capability_codes",
]
