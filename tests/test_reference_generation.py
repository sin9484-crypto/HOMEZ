"""
=========================================================
Homez OS

File : tests/test_reference_generation.py

2026-10-19 — "참고 사진으로 만들기" R1(`reference_generation.py`)을 고정한다. 순수 데이터·계산, 외부 호출·DB·파일 0.

  - 참고 사진 검사: 형식·크기·실제 내용(매직 바이트)·권리. 권리 거부는 항상 막고, 확인 안 된 사진은 사용자 확인이 있을 때만, 모르는 상태는 막는다.
  - 계획: 대표 이미지 금지, 배경·포즈·장수 한도, 같은 입력은 같은 계획·같은 지문, 입력이 바뀌면 지문이 바뀐다.
  - 제공자 요청: FAKE 외 제공자는 거절, 검수 항목·AI 생성 표기·제품 보존 요구가 실린다.
  - 가짜 제공자: 참고 사진이 다르면 결과가 다르고, 참고 사진이 없는 기존 요청의 결과는 그대로다.
=========================================================
"""

import hashlib
import json
import re
import unittest
from pathlib import Path

from app.core.exceptions import BadRequestException
from app.domains.media_asset import reference_generation as rg
from app.domains.media_asset.constants import MAX_IMAGE_FILE_SIZE_BYTES
from app.domains.media_asset.providers import FakeImageGenerationProvider
from app.domains.media_asset.providers import ImageGenerationRequest
from app.domains.media_asset.providers import ImageGenerationRequestItem
from app.domains.media_asset.providers import _deterministic_png
from app.domains.media_asset.reference_generation import ReferenceGenerationError

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app/domains/media_asset/reference_generation.py").read_text(encoding="utf-8")

PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"j" * 32
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"w" * 16


def ref(image=PNG, mime="image/png", rights="VERIFIED", ack=False):
    return rg.check_reference_image(image, mime, rights, ack)


def plan_of(reference=None, purpose="DETAIL", backgrounds=("CAFE",), poses=("ARMS_DOWN",), count=1):
    return rg.build_plan(reference or ref(), purpose, list(backgrounds), list(poses), count)


class PresetTests(unittest.TestCase):

    def test_codes_are_unique_and_well_formed(self):
        for presets in (rg.BACKGROUND_PRESETS, rg.POSE_PRESETS):
            codes = [p.code for p in presets]
            self.assertEqual(len(codes), len(set(codes)))
            for p in presets:
                self.assertRegex(p.code, r"^[A-Z][A-Z_]+$")
                self.assertTrue(p.label_ko.strip())
                self.assertTrue(p.hint.strip())

    def test_video_examples_are_available(self):
        backgrounds = {p.code for p in rg.BACKGROUND_PRESETS}
        poses = {p.code for p in rg.POSE_PRESETS}
        self.assertTrue({"CAFE", "WALKING_PATH", "STREET", "SEASIDE"} <= backgrounds)
        self.assertTrue({"ARMS_DOWN", "BODY_TURN_LEFT", "KEEP_ORIGINAL"} <= poses)

    def test_list_presets_shape(self):
        listing = rg.list_presets()
        self.assertEqual(listing["max_variants"], 6)
        self.assertEqual(len(listing["backgrounds"]), len(rg.BACKGROUND_PRESETS))
        self.assertEqual(len(listing["poses"]), len(rg.POSE_PRESETS))
        self.assertEqual(set(listing["backgrounds"][0]), {"code", "label_ko"})
        json.dumps(listing)

    def test_limits_are_pinned(self):
        self.assertEqual(rg.MAX_VARIANTS_PER_REQUEST, 6)
        self.assertEqual(rg.MAX_BACKGROUNDS, 3)
        self.assertEqual(rg.MAX_POSES, 3)
        self.assertEqual(rg.ALLOWED_PROVIDER_CODES, ("FAKE",))
        self.assertEqual(rg.ALLOWED_PURPOSES, ("MAIN", "ADDITIONAL", "DETAIL"))
        self.assertEqual(rg.MAIN_EXTRA_CHECKS, ("MAIN_IMAGE_SPEC",))
        self.assertEqual(rg.GENERATED_LABEL_KO, "AI 생성 이미지")
        self.assertEqual(
            rg.REQUIRED_REVIEW_CHECKS,
            ("PRODUCT_MATCHES_REFERENCE", "NO_TEXT_OVERLAY", "NO_WATERMARK", "NO_THIRD_PARTY_MARKS", "GENERATED_LABEL_ATTACHED"),
        )
        self.assertEqual(rg.LABEL_PLACEMENT, "OUTSIDE_IMAGE")
        self.assertEqual(rg.FORBIDDEN_IN_IMAGE, ("watermark", "logo", "text overlay", "caption", "signature", "stamp", "label"))


class ReferenceImageTests(unittest.TestCase):

    def test_accepts_each_allowed_format(self):
        for image, mime in ((PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp")):
            result = ref(image, mime)
            self.assertEqual(result.mime_type, mime)
            self.assertEqual(result.sha256_hex, hashlib.sha256(image).hexdigest())
            self.assertEqual(result.image_bytes, image)

    def test_rejects_empty_oversized_and_wrong_type(self):
        for bad in (b"", None, "text"):
            with self.assertRaises(ReferenceGenerationError):
                ref(bad)
        with self.assertRaises(ReferenceGenerationError):
            ref(PNG + b"x" * MAX_IMAGE_FILE_SIZE_BYTES)
        with self.assertRaises(ReferenceGenerationError):
            ref(PNG, "image/gif")

    def test_size_limit_boundary(self):
        exactly = PNG + b"x" * (MAX_IMAGE_FILE_SIZE_BYTES - len(PNG))
        self.assertEqual(len(exactly), MAX_IMAGE_FILE_SIZE_BYTES)
        ref(exactly)
        with self.assertRaises(ReferenceGenerationError):
            ref(exactly + b"x")

    def test_declared_type_must_match_content(self):
        with self.assertRaises(ReferenceGenerationError):
            ref(PNG, "image/jpeg")
        with self.assertRaises(ReferenceGenerationError):
            ref(JPEG, "image/png")
        with self.assertRaises(ReferenceGenerationError):
            ref(b"<html>not an image</html>", "image/png")
        with self.assertRaises(ReferenceGenerationError):
            ref(b"RIFF\x00\x00\x00\x00WAVE" + b"w" * 8, "image/webp")

    def test_denied_rights_always_blocked_even_when_acknowledged(self):
        with self.assertRaises(ReferenceGenerationError) as ctx:
            ref(rights="RIGHTS_DENIED", ack=True)
        # '모르는 상태' 거절과 구별된다 — 금지된 사진이라는 이유가 그대로 전달된다.
        self.assertIn("금지", str(ctx.exception.detail))

    def test_unknown_type_message_names_allowed_formats(self):
        with self.assertRaises(ReferenceGenerationError) as ctx:
            ref(PNG, "image/gif")
        self.assertIn("PNG·JPEG·WEBP", str(ctx.exception.detail))

    def test_unverified_needs_acknowledgement(self):
        with self.assertRaises(ReferenceGenerationError):
            ref(rights="RIGHTS_UNVERIFIED", ack=False)
        for not_true in (None, 1, "yes", "true"):
            with self.assertRaises(ReferenceGenerationError):
                ref(rights="RIGHTS_UNVERIFIED", ack=not_true)
        result = ref(rights="RIGHTS_UNVERIFIED", ack=True)
        self.assertTrue(result.rights_acknowledged)

    def test_unknown_rights_status_blocked(self):
        for status in ("", "UNKNOWN", "verified", None, "OWNED"):
            with self.assertRaises(ReferenceGenerationError):
                ref(rights=status, ack=True)

    def test_error_is_a_bad_request(self):
        with self.assertRaises(BadRequestException):
            ref(b"")


class PlanTests(unittest.TestCase):

    def test_main_image_is_allowed_but_flagged(self):
        # 2026-10-19 사용자 결정: 대표도 허용. 대신 경고·대표 규격 검수·사용자 최종 확인 요구가 자동으로 붙는다.
        plan = plan_of(purpose="MAIN")
        self.assertEqual(plan.purpose, "MAIN")
        self.assertTrue(plan.requires_user_confirmation)
        self.assertEqual(plan.required_checks, rg.REQUIRED_REVIEW_CHECKS + ("MAIN_IMAGE_SPEC",))
        self.assertEqual(plan.warnings, (rg.MAIN_POLICY_WARNING_KO,))
        self.assertIn("반려", plan.warnings[0])
        self.assertIn("확인하지 못했습니다", plan.warnings[0])

    def test_main_still_needs_a_clean_reference_and_valid_selection(self):
        with self.assertRaises(ReferenceGenerationError):
            ref(rights="RIGHTS_DENIED", ack=True)
        with self.assertRaises(ReferenceGenerationError):
            plan_of(purpose="MAIN", backgrounds=("MOON",))
        with self.assertRaises(ReferenceGenerationError):
            plan_of(purpose="MAIN", count=7)

    def test_non_main_purposes_carry_no_main_extras(self):
        for purpose in ("ADDITIONAL", "DETAIL"):
            plan = plan_of(purpose=purpose)
            self.assertFalse(plan.requires_user_confirmation)
            self.assertEqual(plan.warnings, ())
            self.assertEqual(plan.required_checks, rg.REQUIRED_REVIEW_CHECKS)

    def test_unknown_purpose_refused(self):
        for purpose in ("", "THUMB", "main", None):
            with self.assertRaises(ReferenceGenerationError):
                plan_of(purpose=purpose)

    def test_additional_and_detail_allowed(self):
        for purpose in ("ADDITIONAL", "DETAIL"):
            self.assertEqual(plan_of(purpose=purpose).purpose, purpose)

    def test_selection_rules(self):
        with self.assertRaises(ReferenceGenerationError):
            plan_of(backgrounds=())
        with self.assertRaises(ReferenceGenerationError):
            plan_of(poses=())
        with self.assertRaises(ReferenceGenerationError):
            plan_of(backgrounds=("CAFE", "STREET", "SEASIDE", "HOME_INDOOR"))
        with self.assertRaises(ReferenceGenerationError):
            plan_of(poses=("ARMS_DOWN", "HAND_ON_HIP", "WALKING", "KEEP_ORIGINAL"))
        with self.assertRaises(ReferenceGenerationError):
            plan_of(backgrounds=("MOON",))
        with self.assertRaises(ReferenceGenerationError):
            plan_of(poses=("BREAKDANCE",))

    def test_boundary_of_three_backgrounds_and_poses(self):
        plan = plan_of(backgrounds=("CAFE", "STREET", "SEASIDE"), poses=("ARMS_DOWN", "HAND_ON_HIP", "WALKING"), count=6)
        self.assertEqual(len(plan.variants), 6)

    def test_count_rules(self):
        for bad in (0, -1, True, 1.5, "2", None, 7):
            with self.assertRaises(ReferenceGenerationError, msg=repr(bad)):
                plan_of(backgrounds=("CAFE", "STREET", "SEASIDE"), poses=("ARMS_DOWN", "HAND_ON_HIP", "WALKING"), count=bad)
        with self.assertRaises(ReferenceGenerationError):
            plan_of(backgrounds=("CAFE",), poses=("ARMS_DOWN",), count=2)  # 조합이 1개뿐

    def test_variants_come_in_a_fixed_order(self):
        plan = plan_of(backgrounds=("CAFE", "STREET"), poses=("ARMS_DOWN", "HAND_ON_HIP"), count=3)
        self.assertEqual(
            [(v.sequence_index, v.background, v.pose) for v in plan.variants],
            [(0, "CAFE", "ARMS_DOWN"), (1, "CAFE", "HAND_ON_HIP"), (2, "STREET", "ARMS_DOWN")],
        )

    def test_duplicate_selections_do_not_change_the_plan(self):
        a = plan_of(backgrounds=("CAFE", "CAFE", "STREET"), poses=("ARMS_DOWN", "ARMS_DOWN"), count=2)
        b = plan_of(backgrounds=("CAFE", "STREET"), poses=("ARMS_DOWN",), count=2)
        self.assertEqual(a.request_fingerprint, b.request_fingerprint)
        self.assertEqual(a.variants, b.variants)

    def test_same_input_same_fingerprint_and_inputs_change_it(self):
        base = plan_of(backgrounds=("CAFE", "STREET"), poses=("ARMS_DOWN",), count=2)
        again = plan_of(backgrounds=("CAFE", "STREET"), poses=("ARMS_DOWN",), count=2)
        self.assertEqual(base.request_fingerprint, again.request_fingerprint)
        self.assertRegex(base.request_fingerprint, r"^[0-9a-f]{64}$")

        others = {
            "reference": plan_of(reference=ref(JPEG, "image/jpeg"), backgrounds=("CAFE", "STREET"), poses=("ARMS_DOWN",), count=2),
            "purpose": plan_of(purpose="ADDITIONAL", backgrounds=("CAFE", "STREET"), poses=("ARMS_DOWN",), count=2),
            "background": plan_of(backgrounds=("CAFE", "SEASIDE"), poses=("ARMS_DOWN",), count=2),
            "pose": plan_of(backgrounds=("CAFE", "STREET"), poses=("HAND_ON_HIP",), count=2),
            "count": plan_of(backgrounds=("CAFE", "STREET"), poses=("ARMS_DOWN",), count=1),
            "order": plan_of(backgrounds=("STREET", "CAFE"), poses=("ARMS_DOWN",), count=2),
        }
        for name, other in others.items():
            self.assertNotEqual(base.request_fingerprint, other.request_fingerprint, name)

    def test_rights_state_does_not_change_the_fingerprint(self):
        verified = plan_of(reference=ref(rights="VERIFIED"))
        acknowledged = plan_of(reference=ref(rights="RIGHTS_UNVERIFIED", ack=True))
        self.assertEqual(verified.request_fingerprint, acknowledged.request_fingerprint)

    def test_plan_carries_label_and_checks(self):
        plan = plan_of()
        self.assertEqual(plan.generated_label, "AI 생성 이미지")
        self.assertEqual(plan.required_checks, rg.REQUIRED_REVIEW_CHECKS)
        self.assertEqual(plan.version, rg.REFERENCE_GENERATION_VERSION)

    def test_summary_is_json_and_has_no_image_bytes(self):
        reference = ref()
        summary = plan_of(reference=reference, purpose="MAIN", backgrounds=("CAFE", "STREET"), poses=("ARMS_DOWN",), count=2).to_summary()
        self.assertTrue(summary["requires_user_confirmation"])
        self.assertEqual(len(summary["warnings"]), 1)
        self.assertIn("MAIN_IMAGE_SPEC", summary["required_checks"])
        text = json.dumps(summary, ensure_ascii=False)
        self.assertEqual(summary["count"], 2)
        self.assertNotIn(reference.sha256_hex, text)
        self.assertNotIn("image_bytes", text)


class ProviderRequestTests(unittest.TestCase):

    def build(self, **kwargs):
        reference = ref()
        plan = plan_of(reference=reference, backgrounds=("CAFE", "STREET"), poses=("ARMS_DOWN", "BODY_TURN_LEFT"), count=3)
        return plan, reference, rg.to_provider_request(plan, reference, company_id=7, product_candidate_id=11, **kwargs)

    def test_request_fields(self):
        plan, reference, request = self.build()
        self.assertEqual((request.company_id, request.product_candidate_id), (7, 11))
        self.assertEqual(request.prompt_fingerprint, plan.request_fingerprint)
        self.assertEqual(len(request.items), 3)
        self.assertEqual([i.sequence_index for i in request.items], [0, 1, 2])
        self.assertTrue(all(i.purpose == "DETAIL" for i in request.items))
        self.assertEqual(request.reference_image_bytes, reference.image_bytes)
        self.assertEqual(request.reference_mime_type, "image/png")

    def test_style_params_carry_requirements(self):
        _, _, request = self.build()
        params = request.style_params
        self.assertEqual(params["kind"], "REFERENCE_GENERATION")
        self.assertIs(params["preserve_product"], True)
        self.assertEqual(params["generated_label"], "AI 생성 이미지")
        self.assertEqual(params["required_checks"], list(rg.REQUIRED_REVIEW_CHECKS))
        self.assertEqual(len(params["variants"]), 3)
        first = params["variants"][0]
        self.assertEqual((first["background"], first["pose"]), ("CAFE", "ARMS_DOWN"))
        self.assertTrue(first["background_hint"] and first["pose_hint"])
        json.dumps(params)

    def test_every_purpose_forbids_watermark_and_burned_in_label(self):
        # 사용자 지시(2026-10-19): 이미지 생성에 워터마크를 만들지 않는다. 대표·추가·상세 모두 같다.
        reference = ref()
        for purpose in ("MAIN", "ADDITIONAL", "DETAIL"):
            plan = plan_of(reference=reference, purpose=purpose)
            self.assertIn("NO_WATERMARK", plan.required_checks, purpose)
            params = rg.to_provider_request(plan, reference, 1, 1).style_params
            self.assertIs(params["no_watermark"], True, purpose)
            self.assertIn("watermark", params["forbidden_in_image"], purpose)
            self.assertIn("text overlay", params["forbidden_in_image"], purpose)
            self.assertEqual(params["label_placement"], "OUTSIDE_IMAGE", purpose)
            self.assertEqual(plan.to_summary()["label_placement"], "OUTSIDE_IMAGE")
            self.assertIs(plan.to_summary()["no_watermark"], True)

    def test_prompt_hints_never_ask_for_text_or_marks(self):
        # 장면·포즈 설명 문구가 글자·표식을 요구하면 안 된다.
        for preset in rg.BACKGROUND_PRESETS + rg.POSE_PRESETS:
            for word in ("text", "watermark", "logo", "caption", "sign", "brand", "label", "stamp"):
                self.assertNotIn(word, preset.hint.lower(), preset.code)

    def test_only_fake_provider_is_allowed(self):
        plan, reference, _ = self.build()
        for code in ("DISABLED", "OPENAI", "REAL", "", "fake"):
            with self.assertRaises(ReferenceGenerationError, msg=code):
                rg.to_provider_request(plan, reference, 1, 1, provider_code=code)

    def test_main_request_carries_confirmation_and_warning(self):
        reference = ref()
        plan = plan_of(reference=reference, purpose="MAIN")
        request = rg.to_provider_request(plan, reference, 1, 1)
        self.assertEqual({item.purpose for item in request.items}, {"MAIN"})
        self.assertEqual(request.style_params["purpose"], "MAIN")
        self.assertIs(request.style_params["requires_user_confirmation"], True)
        self.assertEqual(request.style_params["warnings"], [rg.MAIN_POLICY_WARNING_KO])
        self.assertIn("MAIN_IMAGE_SPEC", request.style_params["required_checks"])

    def test_detail_request_has_no_confirmation_flag(self):
        _, _, request = self.build()
        self.assertIs(request.style_params["requires_user_confirmation"], False)
        self.assertEqual(request.style_params["warnings"], [])
        self.assertEqual(request.style_params["purpose"], "DETAIL")


class FakeProviderTests(unittest.TestCase):

    def test_same_request_same_output(self):
        provider = FakeImageGenerationProvider()
        plan = plan_of(backgrounds=("CAFE", "STREET"), poses=("ARMS_DOWN",), count=2)
        request = rg.to_provider_request(plan, ref(), 1, 1)
        first, second = provider.generate(request), provider.generate(request)
        self.assertEqual([i.image_bytes for i in first.items], [i.image_bytes for i in second.items])
        self.assertEqual(len(first.items), 2)
        self.assertTrue(all(i.status == "SUCCEEDED" for i in first.items))

    def test_different_reference_gives_different_images(self):
        provider = FakeImageGenerationProvider()
        results = []
        for image, mime in ((PNG, "image/png"), (PNG + b"more", "image/png")):
            reference = ref(image, mime)
            plan = plan_of(reference=reference)
            request = rg.to_provider_request(plan, reference, 1, 1)
            results.append(provider.generate(request).items[0].image_bytes)
        self.assertNotEqual(results[0], results[1])

    def test_reference_bytes_alone_change_the_output(self):
        # 요청 지문·항목이 똑같아도 참고 사진 바이트가 다르면 결과가 달라야 한다(지문에만 기대지 않는다).
        from dataclasses import replace
        provider = FakeImageGenerationProvider()
        plan = plan_of()
        request = rg.to_provider_request(plan, ref(), 1, 1)
        other = replace(request, reference_image_bytes=PNG + b"different")
        self.assertEqual(request.prompt_fingerprint, other.prompt_fingerprint)
        self.assertNotEqual(provider.generate(request).items[0].image_bytes, provider.generate(other).items[0].image_bytes)

    def test_request_without_reference_is_unchanged(self):
        provider = FakeImageGenerationProvider()
        request = ImageGenerationRequest(
            company_id=1, product_candidate_id=2, prompt_fingerprint="abc", style_params={},
            items=(ImageGenerationRequestItem(purpose="MAIN", sequence_index=0),),
        )
        self.assertIsNone(request.reference_image_bytes)
        self.assertIsNone(request.reference_mime_type)
        output = provider.generate(request).items[0].image_bytes
        self.assertEqual(output, _deterministic_png("abc:0:MAIN"))


class PurityTests(unittest.TestCase):

    def test_no_network_database_or_file_access(self):
        imports = "\n".join(re.findall(r"^(?:from|import)\s+\S+", SOURCE, flags=re.MULTILINE))
        for forbidden in ("requests", "httpx", "urllib", "socket", "sqlalchemy", "subprocess", "pathlib", "aiohttp"):
            self.assertNotRegex(imports, r"\b" + forbidden + r"\b", forbidden)
        for pattern in (r"\bopen\(", r"\.read_bytes\(", r"\.write_bytes\(", r"\bos\.", r"\bsession\b"):
            self.assertNotRegex(SOURCE, pattern, pattern)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for case in (PresetTests, ReferenceImageTests, PlanTests, ProviderRequestTests, FakeProviderTests, PurityTests):
        suite.addTests(loader.loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
