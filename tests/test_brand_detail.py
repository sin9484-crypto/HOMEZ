"""
2026-11-26 — 상표 체계로 통일한 상세 페이지 두 장(사용자 "평균 90점이 될 정도로 다시 만들어라").

  - 고시 원문에서 표·전성분·주의사항을 그대로 뽑는다(상담 전화번호는 넣지 않는다, 주의사항은 번호 앞에서 나눈다).
  - 판매 문구의 의료·효능·과장 표현은 거절, 장면은 네 장, 가로 780·세로 한도 안.
  - 서비스: 같은 상품의 사진만, 고시가 없으면 거절, 상세 이미지 두 장으로 저장(편집 기록 '상세 이미지').
"""

from __future__ import annotations

import io
import json
import unittest

from PIL import Image

from app.core.exceptions import BadRequestException
from app.domains.media_asset import brand_detail as bd
from app.domains.media_asset import image_edit_record as ier
from app.domains.media_asset.studio_service import StudioService
from tests.test_image_edit_record import Base_
from tests.test_image_edit_record import png

NOTICES = [{"noticeCategoryName": "화장품", "noticeCategoryDetailNames": [
    {"noticeCategoryDetailName": "용량(중량)", "content": "200ml"},
    {"noticeCategoryDetailName": "제조국", "content": "대한민국"},
    {"noticeCategoryDetailName": "화장품법에 따라 기재, 표시하여야 하는 모든 성분", "content": "정제수, 글리세린, 향료"},
    {"noticeCategoryDetailName": "사용할 때 주의사항", "content": "이상이 있으면 상담할 것. 1. 상처 부위에 쓰지 말 것. 2. 눈에 들어가면 씻어낼 것."},
    {"noticeCategoryDetailName": "소비자상담관련 전화번호", "content": "01000000000"},
]}]


def scene_png(colour=(120, 160, 190)):
    out = io.BytesIO()
    Image.new("RGB", (1200, 1200), colour).save(out, format="PNG")
    return out.getvalue()


class PureTests(unittest.TestCase):

    def test_notice_facts_are_taken_word_for_word_without_the_phone_number(self):
        rows, ingredients, cautions = bd.notice_facts(NOTICES)
        self.assertEqual([(r.label, r.value) for r in rows], [("용량", "200ml"), ("제조국", "대한민국")])
        self.assertEqual(ingredients, "정제수, 글리세린, 향료")
        self.assertEqual(cautions, ["이상이 있으면 상담할 것.", "1. 상처 부위에 쓰지 말 것.", "2. 눈에 들어가면 씻어낼 것."])
        self.assertNotIn("01000000000", json.dumps([r.value for r in rows], ensure_ascii=False))

    def test_pages_have_the_detail_width_and_fit(self):
        one = Image.open(io.BytesIO(bd.page_one(scene_png())))
        rows, ingredients, cautions = bd.notice_facts(NOTICES)
        two = Image.open(io.BytesIO(bd.page_two([bd.Scene(scene_png(), "돌봄", "침대 곁에서")] * 4, rows, ingredients, cautions)))
        for page in (one, two):
            self.assertEqual(page.width, bd.WIDTH)
            self.assertLessEqual(page.height, bd.MAX_HEIGHT)

    def test_marketing_copy_is_checked_but_legal_text_is_not(self):
        with self.assertRaises(bd.BrandPageError):
            bd.page_one(scene_png(), bd.Copy(sub="피부를 살균하는 바디워시"))
        with self.assertRaises(bd.BrandPageError):
            bd.page_two([bd.Scene(scene_png(), "최고의 선택", "x")] * 4, [], "", [])
        with self.assertRaises(bd.BrandPageError):
            bd.page_two([bd.Scene(scene_png(), "돌봄", "x")] * 3, [], "", [])
        bd.page_two([bd.Scene(scene_png(), "돌봄", "x")] * 4, [bd.InfoRow("주의", "부작용이 있는 경우 상담할 것")], "", ["피부과 전문의 등과 상담할 것."])


class ServiceTests(Base_):

    def setUp(self):
        super().setUp()
        with self.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE listing_wizards (id INTEGER PRIMARY KEY, company_id INTEGER, product_candidate_id INTEGER, channel_selections_json TEXT)")
            conn.exec_driver_sql("INSERT INTO listing_wizards (company_id, product_candidate_id, channel_selections_json) VALUES (?, 7, ?)",
                                 (self.company_id, json.dumps([{"required_fields": {"notices": NOTICES}}], ensure_ascii=False)))

    def test_two_detail_pages_are_saved(self):
        hero = self.upload(scene_png())
        scenes = [self.upload(scene_png((i * 40, 120, 160))) for i in range(4)]
        pages = StudioService(self.db, queue=self.queue).brand_pages(self.company_id, 1, hero.id, [(s.id, "돌봄", "침대 곁에서") for s in scenes])
        self.assertEqual([p.purpose for p in pages], ["DETAIL", "DETAIL"])
        self.assertEqual(self.kinds(pages[0]), [ier.ORIGINAL, ier.DETAIL_PAGE])

    def test_photos_of_another_product_or_no_notice_are_refused(self):
        hero = self.upload(scene_png())
        service = StudioService(self.db, queue=self.queue)
        other = self.queue.upload_original_asset(owner_type="PRODUCT_CANDIDATE", owner_id=99, purpose="MAIN", display_order=0,
                                                 image_bytes=png((1, 2, 3)), original_filename="o.png", company_id=self.company_id)
        self.db.commit()
        scenes = [self.upload(scene_png()) for _ in range(3)] + [other]
        with self.assertRaises(BadRequestException):
            service.brand_pages(self.company_id, 1, hero.id, [(s.id, "돌봄", "x") for s in scenes])
        with self.engine.begin() as conn:
            conn.exec_driver_sql("DELETE FROM listing_wizards")
        ok = [self.upload(scene_png((5, 6, 7))) for _ in range(4)]
        with self.assertRaises(BadRequestException):
            service.brand_pages(self.company_id, 1, hero.id, [(s.id, "돌봄", "x") for s in ok])


if __name__ == "__main__":
    unittest.main()
