"""
=========================================================
Homez OS

File : tests/test_candidate_evaluation.py

2026-10-18 — 상품 서칭 AI S2: 후보 사전 평가 규칙(`candidate_evaluation.py`, 순수 규칙·AI 없음)을 고정한다. 네트워크·DB 없음.

  - 금지 품목·비추천 마진은 제외 권고(BLOCK), 상표·효능·아동·규제 분류·KC·마진 미분석/값 부족·중복은 보류(HOLD), 그 밖은 정보(INFO).
  - **마진 분석이 없으면 후보 가능이 될 수 없다**(모르는 비용을 괜찮다고 보지 않는다). 분석이 후보 판정이고 다른 점검이 모두 통과해야 후보 가능.
  - 이유는 심각한 순서로, 결정은 가장 심각한 수준을 따른다. 읽을 수 없는 스냅샷·알 수 없는 판정은 "분석 없음"으로 본다.
=========================================================
"""

import json
import unittest
from pathlib import Path

from app.domains.product_candidate import candidate_evaluation as ev

SOURCE = (Path(__file__).resolve().parents[1] / "app/domains/product_candidate/candidate_evaluation.py").read_text(encoding="utf-8")

OK_SNAPSHOT = {"v": 1, "verdict": "CANDIDATE", "min_margin_rate": "0.18", "scenarios": [{"t": "0.18", "p": "12790", "m": "0.1802"}], "missing": [], "reasons": ["…"]}
NOTICE = [("용량(중량)", "200ml"), ("제조국", "대한민국")]


def run(name="레이펄스 워터리스 바디워시", **kw):
    kw.setdefault("notice_rows", NOTICE)
    kw.setdefault("snapshot", OK_SNAPSHOT)
    return ev.evaluate(name=name, **kw)


def codes(result, level=None):
    return [r["code"] for r in result["reasons"] if level is None or r["level"] == level]


class DecisionTests(unittest.TestCase):

    def test_everything_clean_with_a_good_margin_analysis_is_ready(self):
        r = run()
        self.assertEqual(r["decision"], ev.READY)
        self.assertEqual(codes(r), ["MARGIN_OK"])
        self.assertEqual(r["reasons"][0]["params"]["price"], "12790")

    def test_no_margin_analysis_can_never_be_ready(self):
        r = run(snapshot=None)
        self.assertEqual((r["decision"], codes(r, ev.HOLD)), (ev.HOLD, ["MARGIN_NOT_ANALYZED"]))

    def test_the_decision_follows_the_most_severe_level(self):
        self.assertEqual(run(name="총기 모양 라이터")["decision"], ev.EXCLUDE)
        self.assertEqual(run(name="총기 모양 나이키 스타일 라이터", snapshot=None)["decision"], ev.EXCLUDE)  # BLOCK이 HOLD보다 앞선다
        self.assertEqual(run(name="나이키 스타일 양말")["decision"], ev.HOLD)

    def test_reasons_are_ordered_from_severe_to_mild(self):
        r = run(name="총기 나이키", snapshot={"verdict": "HOLD", "missing": ["VAT_STATE"]}, notice_rows=None)
        levels = [x["level"] for x in r["reasons"]]
        self.assertEqual(levels, sorted(levels, key=lambda l: {"BLOCK": 0, "HOLD": 1, "INFO": 2, "OK": 3}[l]))
        self.assertEqual(r["reasons"][0]["code"], "PROHIBITED_ITEM")


class NameCheckTests(unittest.TestCase):

    def test_prohibited_words_block(self):
        r = run(name="실탄 모양 키링")
        self.assertEqual((r["decision"], codes(r, ev.BLOCK)), (ev.EXCLUDE, ["PROHIBITED_ITEM"]))
        self.assertIn("실탄", r["reasons"][0]["params"]["words"])

    def test_high_risk_brand_words_hold(self):
        r = run(name="나이키 스타일 러닝 양말")
        self.assertIn("BRAND_IP_WORD", codes(r, ev.HOLD))

    def test_claim_and_child_words_hold(self):
        self.assertIn("CLAIM_WORDS", codes(run(name="무릎 연골 보호 서포터")))
        self.assertIn("CHILD_TERMS", codes(run(name="어린이 키즈 양말")))
        self.assertNotIn("CLAIM_WORDS", codes(run(name="러닝 무릎보호대")))

    def test_the_category_hint_is_also_checked_for_prohibited_items(self):
        r = ev.evaluate(name="평범한 상품", category_hint="총기 액세서리", notice_rows=NOTICE, snapshot=OK_SNAPSHOT)
        self.assertEqual(r["decision"], ev.EXCLUDE)


class NoticeCheckTests(unittest.TestCase):

    def test_regulated_notice_categories_hold(self):
        for value, kind in (("화장품", "COSMETIC"), ("가공식품", "FOOD"), ("건강기능식품", "HEALTH_FOOD"), ("의료기기", "MEDICAL"), ("전기용품", "ELECTRIC"), ("어린이제품", "CHILD")):
            r = run(notice_rows=[("상품정보제공고시 구분", value)])
            found = [x for x in r["reasons"] if x["code"] == "REGULATED_CATEGORY"]
            self.assertEqual([f["params"]["kind"] for f in found], [kind], value)
            self.assertEqual(r["decision"], ev.HOLD)

    def test_an_ordinary_notice_category_is_fine(self):
        self.assertEqual(run(notice_rows=[("상품정보제공고시 구분", "의류")])["decision"], ev.READY)

    def test_a_kc_certification_type_other_than_not_applicable_holds(self):
        for value in ("해당사항없음", "해당 없음", "해당없음", "없음", "-", "해당"):
            self.assertNotIn("KC_CERT", codes(run(notice_rows=[("KC 인증유형", value)])), value)
        self.assertIn("KC_CERT", codes(run(notice_rows=[("KC 인증유형", "안전확인 신고 XU12345")])))

    def test_a_missing_notice_is_only_information(self):
        for rows in (None, []):
            r = run(notice_rows=rows)
            self.assertEqual(codes(r, ev.INFO), ["NO_NOTICE"])
            self.assertEqual(r["decision"], ev.READY)  # 고시가 없다는 사실만으로 막지 않는다


class MarginCheckTests(unittest.TestCase):

    def test_hold_snapshots_list_what_is_missing(self):
        r = run(snapshot={"verdict": "HOLD", "missing": ["SUPPLY_PRICE", "CHANNEL_FEE"], "reasons": ["계산에 필요한 값이 확인되지 않았다"]})
        hold = next(x for x in r["reasons"] if x["code"] == "MARGIN_ANALYSIS_HOLD")
        self.assertEqual(hold["params"]["missing"], ["SUPPLY_PRICE", "CHANNEL_FEE"])
        self.assertEqual(r["decision"], ev.HOLD)

    def test_a_not_recommended_analysis_excludes(self):
        r = run(snapshot={"verdict": "NOT_RECOMMENDED", "reasons": ["입력한 판매가의 마진율 13.53%가 기준 18%보다 낮다"]})
        self.assertEqual((r["decision"], codes(r, ev.BLOCK)), (ev.EXCLUDE, ["MARGIN_ANALYSIS_UNFIT"]))

    def test_unknown_or_missing_verdicts_count_as_no_analysis(self):
        for snapshot in ({}, {"verdict": "WEIRD"}, {"verdict": None}):
            self.assertEqual(codes(run(snapshot=snapshot), ev.HOLD), ["MARGIN_NOT_ANALYZED"], snapshot)

    def test_a_candidate_snapshot_without_scenarios_still_counts_but_shows_no_price(self):
        r = run(snapshot={"verdict": "CANDIDATE", "min_margin_rate": "0.2", "scenarios": []})
        self.assertEqual(r["decision"], ev.READY)
        self.assertIsNone(r["reasons"][0]["params"]["price"])

    def test_high_risk_acknowledgement_is_not_this_modules_job(self):
        # 분석 단계가 보류로 둔 후보(확인 안 한 고위험)는 분석 결과 그대로 보류로 따른다
        self.assertEqual(run(snapshot={"verdict": "HOLD", "missing": []})["decision"], ev.HOLD)


class DuplicateCheckTests(unittest.TestCase):

    def test_a_duplicate_of_an_earlier_candidate_holds_and_names_it(self):
        r = run(duplicate_of=[{"candidate_id": 3, "status": "APPROVED"}])
        found = next(x for x in r["reasons"] if x["code"] == "DUPLICATE_OF")
        self.assertEqual((found["level"], found["params"]), (ev.HOLD, {"candidate_id": 3, "status": "APPROVED"}))
        self.assertEqual(r["decision"], ev.HOLD)

    def test_variants_are_information_only(self):
        r = run(variants=[{"candidate_id": 4}, {"candidate_id": 5}])
        self.assertEqual((r["decision"], codes(r, ev.INFO)), (ev.READY, ["VARIANT_EXISTS"]))

    def test_at_most_three_duplicates_are_listed(self):
        r = run(duplicate_of=[{"candidate_id": i} for i in range(1, 9)])
        self.assertEqual(codes(r).count("DUPLICATE_OF"), 3)


class SnapshotParsingTests(unittest.TestCase):

    def test_the_real_saved_format_is_read(self):
        text = "AI_ANALYSIS_REPORT:(화면 스냅샷) " + json.dumps({"v": 1, "verdict": "HOLD", "missing": ["VAT_STATE"]}, ensure_ascii=False)
        self.assertEqual(ev.parse_snapshot(text)["missing"], ["VAT_STATE"])

    def test_other_or_broken_text_is_not_a_snapshot(self):
        for text in (None, "", "URL_IMPORT_NOTICE:[]", "AI_ANALYSIS_REPORT:(화면 스냅샷) 깨진", "AI_ANALYSIS_REPORT:(x) [1,2]", "AI_ANALYSIS_REPORT:no json"):
            self.assertIsNone(ev.parse_snapshot(text), text)


class BoundaryTests(unittest.TestCase):

    def test_the_module_has_no_database_network_or_ai(self):
        for forbidden in ("sqlalchemy", "requests", "urllib", "socket", "llm_client", "call_llm", "open(", ".commit("):
            self.assertNotIn(forbidden, SOURCE, forbidden)


if __name__ == "__main__":
    unittest.main()
