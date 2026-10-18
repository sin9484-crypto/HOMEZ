"""
=========================================================
Homez OS

File : tests/test_product_similarity.py

2026-10-18 — 상품 서칭 AI S1: 상품명 정규화와 같은 상품/구성만 다른 상품/비슷한 상품 판정(`product_similarity.py`, 순수 규칙·AI 없음)을 고정한다. 네트워크·DB 없음.

  - 정규화: 광고성 말·규격(수량·용량·무게·길이·사이즈·색상)·모델 번호를 걷어 내고 핵심 이름만 남긴다. 단위를 통일한다(L→ml, kg→g, 2P=2개입).
  - 관계: 같은 상품 / 구성만 다른 상품(규격 충돌) / 비슷한 상품(근거 부족) / 다른 상품. 한쪽에만 있는 규격은 충돌이 아니다.
  - 묶기: 같은 상품군을 하나로 묶되 비슷할 뿐인 것은 합치지 않고 따로 알린다.
  - 경계: 이 모듈은 DB·네트워크·AI를 쓰지 않는다.
=========================================================
"""

import unittest
from pathlib import Path

from app.domains.product_candidate import product_similarity as ps

SOURCE = (Path(__file__).resolve().parents[1] / "app/domains/product_candidate/product_similarity.py").read_text(encoding="utf-8")


def rel(a, b):
    return ps.compare(ps.normalize(a), ps.normalize(b))


class NormalizeTests(unittest.TestCase):

    def test_advertising_words_and_brackets_are_dropped_from_the_core(self):
        n = ps.normalize("[무료배송] 당일발송 특가 러닝 무릎보호대")
        self.assertEqual(n.tokens, ("러닝", "무릎보호대"))
        self.assertNotIn("무료배송", n.core)

    def test_quantity_units_are_unified(self):
        for text in ("무릎보호대 2P", "무릎보호대 2p", "무릎보호대 2개입", "무릎보호대 2개", "무릎보호대 2세트", "무릎보호대 2팩"):
            self.assertEqual(ps.normalize(text).specs, {"count:piece": 2}, text)
        self.assertEqual(ps.normalize("주방행주 40매").specs, {"count:sheet": 40})
        self.assertEqual(ps.normalize("양말 5켤레").specs, {"count:pair": 5})

    def test_volume_weight_and_length_are_converted_to_base_units(self):
        self.assertEqual(ps.normalize("샴푸 1.5L").specs, {"volume_ml": 1500.0})
        self.assertEqual(ps.normalize("샴푸 1500ml").specs, {"volume_ml": 1500.0})
        self.assertEqual(ps.normalize("샴푸 500cc").specs, {"volume_ml": 500.0})
        self.assertEqual(ps.normalize("쌀 2kg").specs, {"weight_g": 2000.0})
        self.assertEqual(ps.normalize("쌀 2000g").specs, {"weight_g": 2000.0})
        self.assertEqual(ps.normalize("케이블 1.5m").specs, {"length_mm": 1500.0})
        self.assertEqual(ps.normalize("케이블 150cm").specs, {"length_mm": 1500.0})
        self.assertEqual(ps.normalize("바디워시 200ml").specs, {"volume_ml": 200.0})  # ml이 m(길이)로 잘못 읽히지 않는다

    def test_specs_may_be_given_in_parentheses_or_in_the_extra_text_but_the_extra_text_never_enters_the_core(self):
        self.assertEqual(ps.normalize("바디워시 (200ml)").specs, {"volume_ml": 200.0})
        n = ps.normalize("바디워시 워터리스", "개당 200ml 3개입")
        self.assertEqual(n.specs, {"volume_ml": 200.0, "count:piece": 3})
        self.assertEqual(n.tokens, ("바디워시", "워터리스"))

    def test_colors_and_sizes_are_specs_even_inside_brackets_but_other_bracket_text_is_dropped(self):
        n = ps.normalize("티셔츠 (블랙) [XL] [공식몰 국내발송]")
        self.assertEqual(n.specs["colors"], ["블랙"])
        self.assertEqual(n.specs["size"], ["xl"])
        self.assertEqual(n.tokens, ("티셔츠",))

    def test_model_numbers_are_separated(self):
        n = ps.normalize("오조 무릎보호대 CH5306792 2P")
        self.assertEqual(n.models, frozenset({"ch5306792"}))
        self.assertNotIn("ch5306792", n.core)
        self.assertEqual(ps.normalize("바디워시 200ml").models, frozenset())  # 규격은 모델 번호가 아니다

    def test_a_site_name_tail_after_a_pipe_is_not_part_of_the_name(self):
        a = ps.normalize("레이펄스 워터리스 바디워시 3개입 | 가상 도매몰")
        b = ps.normalize("레이펄스 워터리스 바디워시 3개입")
        self.assertEqual(a.core, b.core)
        self.assertEqual(rel("[무료배송] 레이펄스 바디워시 워터리스 3개입 | 가상 도매몰", "레이펄스 워터리스 바디워시 3개입")["relation"], ps.DUPLICATE)
        self.assertNotEqual(ps.normalize("러닝|무릎 상품").core, ps.normalize("러닝 상품").core)  # 공백 없는 파이프는 이름의 일부로 둔다

    def test_word_order_and_spacing_do_not_change_the_core(self):
        a = ps.normalize("러닝 무릎보호대 슬개건")
        b = ps.normalize("슬개건 무릎보호대 러닝")
        self.assertEqual(a.core, b.core)

    def test_empty_and_odd_inputs_are_safe(self):
        for value in (None, "", "   ", "★☆♥", "123", "ㅋㅋ"):
            n = ps.normalize(value)
            self.assertEqual(n.core == "", n.tokens == ())
        self.assertEqual(ps.core_score(ps.normalize(""), ps.normalize("")), 0.0)


class RelationTests(unittest.TestCase):

    def test_the_same_product_under_different_names_is_a_duplicate(self):
        r = rel("[무료배송] 러닝 무릎보호대 슬개건 2P 블랙", "블랙 슬개건 러닝 무릎 보호대 2개입")
        self.assertEqual(r["relation"], ps.DUPLICATE)
        self.assertIn("SAME_SPECS", r["reasons"])

    def test_a_different_volume_or_quantity_is_a_variant_not_a_duplicate(self):
        r = rel("레이펄스 워터리스 바디워시 200ml 3개입", "레이펄스 워터리스 바디워시 500ml 3개입")
        self.assertEqual(r["relation"], ps.VARIANT)
        self.assertIn("SPEC_DIFFERS:volume_ml", r["reasons"])
        self.assertEqual(rel("무릎보호대 2P", "무릎보호대 4P")["relation"], ps.VARIANT)
        self.assertEqual(rel("무릎보호대 블랙", "무릎보호대 화이트")["conflicts"], ["colors"])

    def test_a_spec_on_only_one_side_is_not_a_conflict(self):
        r = rel("레이펄스 워터리스 바디워시 200ml", "레이펄스 워터리스 바디워시")
        self.assertEqual(r["conflicts"], [])
        self.assertEqual(r["relation"], ps.DUPLICATE)  # 이름이 똑같으면(핵심 이름 일치 1.0) 규격 정보가 없어도 같은 상품으로 본다

    def test_near_but_not_identical_names_without_any_spec_are_only_similar(self):
        r = rel("프리미엄 스테인리스 텀블러 보온 보냉", "프리미엄 스테인리스 텀블러 보온")  # 낱말 하나(보냉) 차이 — 같은 상품이라고 단정하지 않는다
        self.assertEqual((r["relation"], r["reasons"]), (ps.SIMILAR, ["NAME_NEAR_SAME", "NO_SPEC_INFO"]))
        self.assertGreaterEqual(r["core_score"], ps.CORE_NEAR_SAME)
        r2 = rel("러닝 무릎보호대 슬개건 밴드", "러닝 무릎보호대 슬개건 서포터")
        self.assertEqual((r2["relation"], r2["reasons"]), (ps.SIMILAR, ["NAME_PARTLY_SAME"]))

    def test_one_extra_word_with_matching_specs_is_still_a_duplicate_only_when_a_spec_matches(self):
        r = rel("프리미엄 스테인리스 텀블러 보온 보냉 500ml", "프리미엄 스테인리스 텀블러 보온 500ml")
        self.assertEqual(r["relation"], ps.DUPLICATE)  # 같은 용량이라는 근거가 있다
        self.assertIn("SAME_SPECS", r["reasons"])

    def test_different_products_are_different(self):
        self.assertEqual(rel("러닝 무릎보호대", "레이펄스 바디워시")["relation"], ps.DIFFERENT)
        self.assertEqual(rel("", "무릎보호대")["relation"], ps.DIFFERENT)

    def test_the_same_model_number_makes_names_count_as_the_same_product(self):
        r = rel("오조 슬개건 CH5306792", "러닝 무릎 서포터 슬개건 CH5306792")
        self.assertIn("SAME_MODEL", r["reasons"])
        self.assertEqual(r["relation"], ps.DUPLICATE)

    def test_different_model_numbers_with_the_same_name_are_a_variant(self):
        r = rel("슬개건 무릎보호대 AB1234", "슬개건 무릎보호대 CD5678")
        self.assertEqual(r["relation"], ps.VARIANT)
        self.assertIn("SPEC_DIFFERS:model", r["reasons"])

    def test_overlapping_colors_are_not_a_conflict(self):
        r = rel("티셔츠 블랙 화이트 네이비", "티셔츠 블랙")
        self.assertNotIn("colors", r["conflicts"])

    def test_relations_are_symmetric(self):
        pairs = [("러닝 무릎보호대 2P 블랙", "무릎보호대 러닝 2개입 블랙"), ("샴푸 500ml", "샴푸 1L"), ("A 상품 모델", "B 상품 모델")]
        for a, b in pairs:
            self.assertEqual(rel(a, b)["relation"], rel(b, a)["relation"], (a, b))
            self.assertEqual(rel(a, b)["core_score"], rel(b, a)["core_score"], (a, b))


class GroupingTests(unittest.TestCase):

    def items(self, names):
        return [(i + 1, ps.normalize(n)) for i, n in enumerate(names)]

    def test_duplicates_and_variants_form_one_family_and_unrelated_products_stay_out(self):
        names = [
            "레이펄스 워터리스 바디워시 200ml 3개입",     # 1
            "[무료배송] 레이펄스 바디워시 워터리스 3개입 200ml",  # 2 (1과 같은 상품)
            "레이펄스 워터리스 바디워시 500ml 3개입",     # 3 (용량만 다름)
            "러닝 무릎보호대 슬개건 2P",                 # 4 (무관)
        ]
        groups = ps.group_products(self.items(names))
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(groups[0]["members"]), [1, 2, 3])
        self.assertEqual([sorted(s) for s in groups[0]["duplicate_sets"]], [[1, 2]])
        relations = {(p["a"], p["b"]): p["relation"] for p in groups[0]["pairs"]}
        self.assertEqual(relations[(1, 2)], ps.DUPLICATE)
        self.assertEqual(relations[(1, 3)], ps.VARIANT)

    def test_similar_only_pairs_are_reported_but_not_merged(self):
        groups = ps.group_products(self.items(["러닝 무릎보호대 슬개건 밴드", "러닝 무릎보호대 슬개건 서포터", "주방 행주"]))
        self.assertEqual([g["members"] for g in groups if g["members"]], [])
        similar = [p for g in groups for p in g.get("similar_pairs", [])]
        self.assertEqual({(p["a"], p["b"]) for p in similar}, {(1, 2)})

    def test_nothing_related_gives_no_groups(self):
        self.assertEqual(ps.group_products(self.items(["바디워시", "무릎보호대", "주방행주"])), [])
        self.assertEqual(ps.group_products([]), [])

    def test_the_input_size_is_capped(self):
        many = [(i, ps.normalize(f"상품{i} 고유이름{i}")) for i in range(ps.MAX_GROUP_ITEMS + 50)]
        groups = ps.group_products(many)
        self.assertTrue(all(m <= ps.MAX_GROUP_ITEMS for g in groups for m in g["members"]))

    def test_members_are_listed_once_and_in_a_stable_order(self):
        names = ["무릎보호대 슬개건 2P", "슬개건 무릎보호대 2개입", "무릎보호대 슬개건 4P"]
        a = ps.group_products(self.items(names))
        b = ps.group_products(self.items(names))
        self.assertEqual(a, b)
        self.assertEqual(len(a[0]["members"]), len(set(a[0]["members"])))


class BoundaryTests(unittest.TestCase):

    def test_the_module_uses_no_database_network_or_ai(self):
        for forbidden in ("sqlalchemy", "requests", "urllib", "socket", "llm", "open(", "session", ".commit("):
            self.assertNotIn(forbidden, SOURCE, forbidden)


if __name__ == "__main__":
    unittest.main()
