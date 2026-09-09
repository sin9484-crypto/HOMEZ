---
name: homez-product-candidate
description: HOMEZ ProductCandidate 계약(발견, 근거, AI 분석 반영, 추천, 운영자 승인/보류/거절)을 구현하거나 검토할 때 사용한다.
---

# ProductCandidate

## 3계층 분리

1. `ProductCandidate` — 현재 상태 투영(최신 점수 + status)
2. `ProductCandidateEvidence` — append-only 근거(수집 원본 + AI 분석).
   재수집해도 기존 근거를 덮어쓰지 않고 새 행만 추가한다.
3. `ProductCandidateDecision` — append-only 운영자 결정 이력

## 상태 전이

```
DISCOVERED → ANALYZED → RECOMMENDED → APPROVED/HELD/REJECTED
```

- `DECIDABLE = (ANALYZED, RECOMMENDED, HELD)` 상태에서만 approve/hold/
  reject 가능.
- **운영자 승인 없이 APPROVED로 자동 전환하지 않는다**(서비스 레벨에서
  `is_admin` 강제).

## 멱등성 / 동시성 (감사 대응)

- `candidate_key`(source_type+market+source_reference) UNIQUE로 중복
  발견 방지. 동시에 같은 키로 최초 생성을 시도하면 IntegrityError가
  발생하고, rollback 후 실제 승자를 재조회해 반환한다(자동 병합 없음).
- `recommend()`와 `approve()/hold()/reject()`는 **조건부 UPDATE +
  rowcount 검증**으로 상태를 바꾼다(`WHERE status IN 기대상태`). 동시에
  두 결정(예: approve와 reject)이 들어와도 하나만 성공하고 나머지는
  `ConflictException`.

## Repository 규칙

`*_no_commit`(flush만). discover/analysis 반영/recommend/approve/hold/
reject 각각이 서비스에서 정확히 commit 1회로 끝나고, 어떤 예외에서도
rollback된다.

## 검증

`tests/test_product_candidate.py`에 실스레드 동시성 테스트 2종
(approve/reject 경쟁, candidate_key 동시 생성 경쟁) + 부분 실패 rollback
테스트 2종 포함.
