"""
=========================================================
Homez OS

File : tests/test_image_concept_spec.py

2026-10-19 — 이미지 AI I1: 이미지 컨셉 **명세 체계**(`image_concepts.py`의 처리 수준·장면·어울림·검사 항목·생성 표기·명세 검증)를 고정한다. 순수 데이터·계산, 외부 호출 0.

  - 기존 6종은 같은 값으로 옮겨졌다(코드·순서·배경·색·용도·상세 테마가 그대로이고 기존 요약 필드도 그대로).
  - 명세 검증은 규칙마다 거절 시험이 있다: 대표는 흰 배경만, L0는 흰/단색/그라데이션만, 생성(L2)은 승인·표기·검사를 갖춰야 하며 대표에 못 쓴다, 색·대비·어울림 규칙.
  - 틀린 레지스트리는 불러올 때 막힌다(fail-closed). 컨셉 코드가 겹치면 조용히 덮어쓰지 않고 거절한다.
=========================================================
"""

import json
import re
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from app.domains.media_asset import image_concepts as ic
from app.domains.media_asset.image_concepts import ConceptSpecError
from app.domains.media_asset.image_concepts import ConceptTheme
from app.domains.media_asset.image_concepts import ImageConcept
from app.domains.media_asset.image_concepts import validate_registry
from app.domains.media_asset.image_concepts import validate_spec

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app/domains/media_asset/image_concepts.py").read_text(encoding="utf-8")
WHITE = ic.get_concept("CLEAN_WHITE")
BEIGE = ic.get_concept("SOFT_BEIGE")
GRAY = ic.get_concept("CALM_GRAY")


def with_(concept, **changes):
    return replace(concept, **changes)


def problems_text(concept):
    return " | ".join(validate_spec(concept))


class MigrationOfTheSixTests(unittest.TestCase):
    """I1 이전과 같은 값: 기존 호출·화면·합성이 달라지지 않는다."""

    EXPECTED = [
        ("CLEAN_WHITE", "깔끔한 흰 배경", ("MAIN", "ADDITIONAL", "DETAIL"), "WHITE", None, None, (255, 255, 255), (34, 34, 34), (45, 108, 223)),
        ("SOFT_BEIGE", "소프트 베이지", ("ADDITIONAL", "DETAIL"), "GRADIENT", "#FBF7EF", "#EADFCB", (247, 241, 230), (43, 36, 24), (138, 109, 75)),
        ("FRESH_MINT", "청량한 민트", ("ADDITIONAL", "DETAIL"), "GRADIENT", "#F2FBF8", "#D8F1EA", (238, 249, 245), (18, 48, 42), (31, 157, 130)),
        ("BLUSH_PINK", "은은한 핑크", ("ADDITIONAL", "DETAIL"), "GRADIENT", "#FFF6F8", "#FBE3E9", (255, 243, 246), (58, 28, 38), (217, 86, 122)),
        ("CALM_GRAY", "차분한 그레이", ("ADDITIONAL", "DETAIL"), "SOLID_COLOR", "#F1F3F5", None, (241, 243, 245), (31, 41, 51), (55, 65, 81)),
        ("MIDNIGHT_PREMIUM", "미드나이트 프리미엄", ("ADDITIONAL", "DETAIL"), "SOLID_COLOR", "#14171C", None, (20, 23, 28), (243, 244, 246), (212, 178, 106)),
    ]

    def test_codes_order_backgrounds_colors_purposes_and_themes_are_unchanged(self):
        concepts = ic.list_concepts()[:6]       # I3(2026-10-23)에서 L1 장면 컨셉 5종이 뒤에 추가됐다 — 처음 6종은 그대로여야 한다
        self.assertEqual(len(ic.list_concepts()), 11)
        self.assertEqual([c.code for c in ic.list_concepts()[6:]], ["LINEN_NATURAL", "WOOD_WARM", "PAPER_SOFT", "SPOTLIGHT_WARM", "SPOTLIGHT_COOL"])
        for concept, (code, label, usable, kind, start, end, bg, text, accent) in zip(concepts, self.EXPECTED):
            self.assertEqual((concept.code, concept.label_ko, concept.usable_for, concept.background_kind, concept.color_hex, concept.color_hex_end),
                             (code, label, usable, kind, start, end), code)
            self.assertEqual((concept.theme.bg, concept.theme.text, concept.theme.accent), (bg, text, accent), code)

    def test_the_old_summary_fields_are_unchanged_and_only_new_ones_were_added(self):
        summary = WHITE.to_summary()
        self.assertEqual({k: summary[k] for k in ("code", "label_ko", "description", "usable_for", "preview")},
                         {"code": "CLEAN_WHITE", "label_ko": "깔끔한 흰 배경", "description": "쿠팡 대표 이미지에 가장 안전한 흰 배경. 군더더기 없는 기본형.",
                          "usable_for": ["MAIN", "ADDITIONAL", "DETAIL"], "preview": {"background": ["#FFFFFF"], "text": "#222222", "accent": "#2D6CDF"}})
        self.assertEqual(set(summary) - {"code", "label_ko", "description", "usable_for", "preview"},
                         {"version", "processing_level", "scene_kind", "mood", "good_for", "avoid_for", "needs_generated_label", "scene_code"})
        json.dumps([c.to_summary() for c in ic.list_concepts()])   # 화면용 요약은 JSON으로 나간다

    def test_a_concept_built_the_old_way_still_works_with_defaults(self):
        old = ImageConcept(
            code="OLD_STYLE", label_ko="예전 방식", description="새 필드 없이 만든 컨셉", usable_for=("ADDITIONAL",), background_kind="SOLID_COLOR",
            color_hex="#EEEEEE", color_hex_end=None, theme=ConceptTheme(bg=(238, 238, 238), text=(0, 0, 0), muted=(60, 60, 60), shade=(220, 220, 220), accent=(0, 60, 160)),
        )
        self.assertEqual((old.version, old.processing_level, old.scene_kind, old.good_for, old.checks, old.needs_generated_label, old.l2_approved), (1, "L0", "", (), (), False, False))
        self.assertEqual(old.background_spec().kind, "SOLID_COLOR")

    def test_the_six_are_all_local_composition_without_generation_or_props(self):
        for concept in ic.list_concepts()[:6]:
            self.assertEqual((concept.processing_level, concept.version, concept.props_allowed, concept.needs_generated_label, concept.l2_approved), ("L0", 1, False, False, False), concept.code)
            self.assertIn(concept.scene_kind, ic.L0_SCENES)
            self.assertIn("PRODUCT_PRESERVATION", concept.checks)
            self.assertTrue(concept.mood)

    def test_only_the_white_concept_requires_the_main_image_spec_check(self):
        self.assertEqual([c.code for c in ic.list_concepts() if "MAIN_IMAGE_SPEC" in c.checks], ["CLEAN_WHITE"])

    def test_the_registry_is_valid_and_the_old_rules_still_hold(self):
        self.assertEqual(validate_registry(), {})
        self.assertEqual([c.code for c in ic.list_concepts("MAIN")], ["CLEAN_WHITE"])
        with self.assertRaises(Exception):
            ic.require_usable("SOFT_BEIGE", "MAIN")


class SpecRuleTests(unittest.TestCase):

    def test_the_six_pass_and_a_plain_copy_passes(self):
        for concept in ic.list_concepts():
            self.assertEqual(validate_spec(concept), [], concept.code)
        self.assertEqual(validate_spec(with_(BEIGE, code="OTHER_BEIGE")), [])

    def test_basic_identity_rules(self):
        for code in ("clean", "AB", "1ABC", "A" * 41, "WITH SPACE", ""):
            self.assertIn("코드", problems_text(with_(BEIGE, code=code)), code)
        self.assertIn("이름과 설명", problems_text(with_(BEIGE, label_ko=" ")))
        self.assertIn("이름과 설명", problems_text(with_(BEIGE, description="")))
        for version in (0, -1, True, "1"):
            self.assertIn("버전", problems_text(with_(BEIGE, version=version)), str(version))

    def test_purposes(self):
        for usable in ((), ("ADDITIONAL", "ADDITIONAL"), ("BANNER",), ("ADDITIONAL", "BANNER")):
            self.assertIn("용도", problems_text(with_(BEIGE, usable_for=usable)), str(usable))

    def test_l0_can_only_compose_white_solid_or_gradient(self):
        for scene in ("MATERIAL_TEXTURE", "LIFESTYLE_SCENE", "STUDIO_LIGHT", "SEASONAL"):
            self.assertIn("L1 이상", problems_text(with_(BEIGE, scene_kind=scene)), scene)
        self.assertIn("장면 종류가 올바르지", problems_text(with_(BEIGE, scene_kind="MARS")))
        self.assertIn("배경 종류와 맞지", problems_text(with_(BEIGE, scene_kind="SOLID")))
        self.assertIn("처리 수준", problems_text(with_(BEIGE, processing_level="L9")))

    def test_a_texture_scene_is_acceptable_only_from_l1(self):
        l1 = with_(BEIGE, code="WOOD_TABLE", processing_level="L1", scene_kind="MATERIAL_TEXTURE")
        self.assertEqual(validate_spec(l1), [])
        self.assertIn("컨셉의 배경 종류", problems_text(with_(l1, background_kind="EXISTING_ASSET")))

    def test_colors_must_fit_the_background_kind(self):
        self.assertIn("흰 배경에는 색", problems_text(with_(WHITE, color_hex="#FFFFFF")))
        self.assertIn("단색 배경", problems_text(with_(GRAY, color_hex=None)))
        self.assertIn("단색 배경", problems_text(with_(GRAY, color_hex_end="#FFFFFF")))
        self.assertIn("단색 배경", problems_text(with_(GRAY, color_hex="F1F3F5")))
        self.assertIn("그라데이션", problems_text(with_(BEIGE, color_hex_end=None)))
        self.assertIn("그라데이션", problems_text(with_(BEIGE, color_hex="#XYZXYZ")))

    def test_main_image_is_white_only_and_never_generated(self):
        self.assertIn("대표 이미지(MAIN)에 쓰는 컨셉은 흰 배경", problems_text(with_(BEIGE, usable_for=("MAIN", "DETAIL"))))
        self.assertIn("대표 이미지(MAIN)에 쓰는 컨셉은 흰 배경", problems_text(with_(GRAY, usable_for=("MAIN",))))
        self.assertIn("규격 검사", problems_text(with_(WHITE, checks=("PRODUCT_PRESERVATION", "BACKGROUND_CONTRAST"))))
        approved_l2_main = with_(WHITE, processing_level="L2", needs_generated_label=True, l2_approved=True, checks=("PRODUCT_PRESERVATION", "MAIN_IMAGE_SPEC", "POLICY_FLAGS", "NO_TEXT_OVERLAY"))
        self.assertIn("대표 이미지에 쓸 수 없다", problems_text(approved_l2_main))

    def generated(self, **changes):
        base = dict(code="STUDIO_SCENE", processing_level="L2", scene_kind="LIFESTYLE_SCENE", needs_generated_label=True, l2_approved=True,
                    usable_for=("ADDITIONAL", "DETAIL"), checks=("PRODUCT_PRESERVATION", "BACKGROUND_CONTRAST", "POLICY_FLAGS", "NO_TEXT_OVERLAY"), props_allowed=True)
        base.update(changes)
        return with_(BEIGE, **base)

    def test_generation_needs_approval_a_label_and_policy_checks(self):
        self.assertEqual(validate_spec(self.generated()), [])
        self.assertIn("별도 승인", problems_text(self.generated(l2_approved=False)))
        self.assertIn("표기가 필요", problems_text(self.generated(needs_generated_label=False)))
        self.assertIn("POLICY_FLAGS", problems_text(self.generated(checks=("PRODUCT_PRESERVATION", "NO_TEXT_OVERLAY"))))
        self.assertIn("NO_TEXT_OVERLAY", problems_text(self.generated(checks=("PRODUCT_PRESERVATION", "POLICY_FLAGS"))))

    def test_non_generated_concepts_cannot_carry_generation_markers_or_props(self):
        for changes in ({"needs_generated_label": True}, {"l2_approved": True}):
            self.assertIn("생성이 아닌 컨셉", problems_text(with_(BEIGE, **changes)), str(changes))
        self.assertIn("소품", problems_text(with_(BEIGE, props_allowed=True)))
        self.assertIn("소품", problems_text(with_(BEIGE, processing_level="L1", scene_kind="STUDIO_LIGHT", props_allowed=True)))

    def test_check_codes(self):
        self.assertIn("알려진 코드", problems_text(with_(BEIGE, checks=("PRODUCT_PRESERVATION", "MAGIC"))))
        self.assertIn("중복 없이", problems_text(with_(BEIGE, checks=("PRODUCT_PRESERVATION", "PRODUCT_PRESERVATION"))))
        self.assertIn("제품 보존 검사", problems_text(with_(BEIGE, checks=("BACKGROUND_CONTRAST",))))
        self.assertIn("제품 보존 검사", problems_text(with_(BEIGE, checks=())))

    def test_suitability_rules(self):
        self.assertIn("겹친다", problems_text(with_(BEIGE, good_for=("식품",), avoid_for=("식품",))))
        self.assertIn("good_for", problems_text(with_(BEIGE, good_for=tuple(f"상품{i}" for i in range(9)))))
        self.assertIn("good_for", problems_text(with_(BEIGE, good_for=("가" * 21,))))
        self.assertIn("avoid_for", problems_text(with_(BEIGE, avoid_for=(" ",))))
        self.assertIn("avoid_for", problems_text(with_(BEIGE, avoid_for=("식품", "식품"))))

    def test_text_stays_readable_on_every_background(self):
        light = ConceptTheme(bg=(255, 255, 255), text=(0, 0, 0), muted=(80, 80, 80), shade=(240, 240, 240), accent=(0, 60, 160))
        self.assertEqual(validate_spec(with_(BEIGE, theme=light)), [])
        self.assertIn("본문 글자", problems_text(with_(BEIGE, theme=replace(light, text=(150, 150, 150)))))
        self.assertIn("보조 글자", problems_text(with_(BEIGE, theme=replace(light, muted=(170, 170, 170)))))
        self.assertIn("포인트 색", problems_text(with_(BEIGE, theme=replace(light, accent=(235, 235, 235)))))


class RegistryTests(unittest.TestCase):

    def test_duplicate_codes_are_refused_not_silently_merged(self):
        with self.assertRaises(ConceptSpecError):
            ic._build_registry([BEIGE, with_(GRAY, code="SOFT_BEIGE")])
        self.assertEqual(list(ic._build_registry([BEIGE, GRAY])), ["SOFT_BEIGE", "CALM_GRAY"])

    def test_registry_checks_report_per_concept_and_overall_problems(self):
        bad = {**ic.CONCEPTS, "BROKEN_ONE": with_(BEIGE, code="BROKEN_ONE", processing_level="L2")}
        found = validate_registry(bad)
        self.assertEqual(list(found), ["BROKEN_ONE"])
        self.assertTrue(any("별도 승인" in p for p in found["BROKEN_ONE"]))
        self.assertIn("레지스트리 키", " ".join(validate_registry({"WRONG_KEY": BEIGE, **{k: v for k, v in ic.CONCEPTS.items() if k != "SOFT_BEIGE"}})["WRONG_KEY"]))
        no_default = {k: v for k, v in ic.CONCEPTS.items() if k != ic.DEFAULT_CONCEPT_CODE}
        self.assertIn("기본 컨셉", " ".join(validate_registry(no_default)["*"]))

    def test_a_registry_without_any_main_concept_is_refused(self):
        only_beige = {"SOFT_BEIGE": BEIGE}
        self.assertTrue(any("대표 이미지에 쓸 수 있는 컨셉이 하나도 없다" in p for p in validate_registry(only_beige)["*"]))

    def test_a_broken_registry_cannot_be_used_fail_closed(self):
        broken = {**ic.CONCEPTS, "BROKEN_ONE": with_(BEIGE, code="BROKEN_ONE", usable_for=("MAIN",))}
        with mock.patch.object(ic, "CONCEPTS", broken):
            with self.assertRaises(ConceptSpecError) as raised:
                ic._assert_registry_valid()
        self.assertIn("BROKEN_ONE", str(raised.exception))
        ic._assert_registry_valid()   # 원래 레지스트리는 통과한다

    def test_the_module_checks_itself_when_it_is_loaded(self):
        self.assertTrue(re.search(r"^_assert_registry_valid\(\)", SOURCE, re.M))
        self.assertLess(SOURCE.index("_assert_registry_valid()   #"), SOURCE.index("__all__ = ["))

    def test_the_module_stays_pure_data(self):
        for forbidden in (r"\brequests\b", r"urllib", r"socket", r"subprocess", r"\bopen\(", r"sqlalchemy", r"call_llm", r"\bprint\("):
            self.assertIsNone(re.search(forbidden, SOURCE), forbidden)


if __name__ == "__main__":
    unittest.main()
