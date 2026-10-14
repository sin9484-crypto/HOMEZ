"""
=========================================================
Homez OS

File : tests/test_media_section_split.py

섹션 경계 분할(`app/domains/media_asset/section_split.py`)과 그 서비스 연결(`mode="SECTION"`)을 고정한다.
Pillow 합성 이미지·임시 SQLite만 쓴다 — 네트워크 없음.
=========================================================
"""

import io
import os
import random
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from PIL import ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.media_asset import section_split as ss
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.format_analyzer import BLANK_TOLERANCE
from app.domains.media_asset.image_split import split_tall_image
from app.domains.media_asset.job_queue_service import ImageGenerationJobQueueService
from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset
from app.domains.user.model import User  # noqa: F401

WIDTH = 780


def png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def blocky(width: int, height: int, seed: int, cell: int = 24) -> Image.Image:
    rng = random.Random(seed)
    small = Image.new("RGB", (max(1, width // cell), max(1, height // cell)))
    small.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(small.width * small.height)])
    return small.resize((width, height), Image.NEAREST)


def page(blocks: list[int], gap: int = 120, width: int = WIDTH, seed: int = 1) -> Image.Image:
    """블록 높이 목록을 흰 여백(gap)으로 사이를 띄워 쌓은 긴 상세 이미지."""

    total = sum(blocks) + gap * (len(blocks) + 1)
    canvas = Image.new("RGB", (width, total), "white")
    y = gap
    for index, height in enumerate(blocks):
        canvas.paste(blocky(width, height, seed + index), (0, y))
        y += height + gap
    return canvas


def row_is_blank(image: Image.Image, y: int) -> bool:
    row = image.convert("L").crop((0, y, image.width, y + 1)).tobytes()
    return max(row) - min(row) <= BLANK_TOLERANCE


class SplitBehaviourTests(unittest.TestCase):

    def setUp(self):
        self.source = page([700, 500, 900, 600, 800, 450, 1000, 520])   # 약 7,000px
        self.segments = ss.split_by_sections(png(self.source))
        self.scaled = self.source.resize((WIDTH, self.source.height), Image.LANCZOS) if self.source.width != WIDTH else self.source

    def test_no_pixel_is_lost_or_changed(self):
        rebuilt = Image.new("RGB", (WIDTH, sum(s.height for s in self.segments)))
        y = 0
        for segment in self.segments:
            piece = Image.open(io.BytesIO(segment.image_bytes))
            self.assertEqual(piece.width, WIDTH)
            rebuilt.paste(piece, (0, y))
            y += piece.height
        self.assertEqual(rebuilt.size, self.scaled.size)
        self.assertEqual(rebuilt.tobytes(), self.scaled.convert("RGB").tobytes())

    def test_segment_bounds_are_contiguous_and_cover_the_whole_image(self):
        self.assertEqual(self.segments[0].start, 0)
        for before, after in zip(self.segments, self.segments[1:]):
            self.assertEqual(before.end, after.start)
        self.assertEqual(self.segments[-1].end, self.scaled.height)
        self.assertEqual(self.segments[-1].cut_end, ss.CUT_END)

    def test_heights_stay_within_the_limits(self):
        for segment in self.segments:
            self.assertGreaterEqual(segment.height, 600)
            self.assertLessEqual(segment.height, 1500)

    def test_every_cut_falls_in_a_blank_band_so_no_content_is_split(self):
        for segment in self.segments[:-1]:
            # 넓은 여백의 가운데(SECTION) 또는 좁은 빈 줄(THIN_GAP) — 어느 쪽이든 내용을 자르지 않는다. FORCED는 없어야 한다.
            self.assertIn(segment.cut_end, (ss.CUT_SECTION, ss.CUT_THIN_GAP))
            self.assertTrue(row_is_blank(self.scaled, segment.end), f"y={segment.end}")
        kinds = [s.cut_end for s in self.segments[:-1]]
        self.assertGreaterEqual(kinds.count(ss.CUT_SECTION), len(kinds) - 1)

    def test_the_fixed_height_split_does_cut_through_content_on_the_same_image(self):
        """이 이미지에서 고정 높이 분할은 내용을 자르고(= 개선의 근거), 섹션 분할은 자르지 않는다."""

        fixed = split_tall_image(png(self.source), segment_height=1200)
        y = 0
        cut_rows = []
        for piece in fixed[:-1]:
            y += Image.open(io.BytesIO(piece)).height
            cut_rows.append(y)
        self.assertTrue(any(not row_is_blank(self.scaled, r) for r in cut_rows))

    def test_cuts_are_close_to_the_preferred_height(self):
        for segment in self.segments[:-1]:
            self.assertLessEqual(abs(segment.height - 1200), 600)


class EdgeCaseTests(unittest.TestCase):

    def test_a_short_image_is_one_segment(self):
        segments = ss.split_by_sections(png(page([500, 300])))
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].cut_end, ss.CUT_END)

    def test_a_wider_image_is_resized_to_the_detail_width(self):
        segments = ss.split_by_sections(png(page([900, 900, 900], width=1000)))
        for segment in segments:
            self.assertEqual(Image.open(io.BytesIO(segment.image_bytes)).width, WIDTH)

    def test_one_huge_block_is_force_cut_and_flagged_not_hidden(self):
        canvas = Image.new("RGB", (WIDTH, 4400), "white")
        canvas.paste(blocky(WIDTH, 4000, 5), (0, 200))
        segments = ss.split_by_sections(png(canvas))
        self.assertGreaterEqual(len(segments), 3)
        self.assertIn(ss.CUT_FORCED, [s.cut_end for s in segments[:-1]])
        for segment in segments:
            self.assertLessEqual(segment.height, 1500)

    def test_no_tiny_trailing_segment_is_left_over(self):
        # 마지막 블록이 작아도(자투리) 모든 조각이 600~1500px 안에 들어온다.
        segments = ss.split_by_sections(png(page([1300, 1300, 300], gap=100)))
        self.assertGreaterEqual(len(segments), 2)
        for segment in segments:
            self.assertGreaterEqual(segment.height, 600)
            self.assertLessEqual(segment.height, 1500)

    def test_plan_is_deterministic(self):
        data = png(page([700, 500, 900, 600, 800]))
        first = [(s.start, s.end, s.cut_end) for s in ss.split_by_sections(data)]
        second = [(s.start, s.end, s.cut_end) for s in ss.split_by_sections(data)]
        self.assertEqual(first, second)

    def test_invalid_input_is_rejected(self):
        with self.assertRaises(BadRequestException):
            ss.split_by_sections(b"not an image")
        with self.assertRaises(BadRequestException):
            ss.split_by_sections(png(page([500, 500])), segment_height=100)
        with self.assertRaises(BadRequestException):
            ss.split_by_sections(png(page([500, 500])), segment_height=3000)


AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, "
    "description VARCHAR(500), ip_address VARCHAR(50))"
)


class ServiceWiringTests(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=self.engine, tables=[
            Company.__table__, MediaAsset.__table__, ImageGenerationJob.__table__,
            ImageGenerationResult.__table__, ImageGenerationDailyUsage.__table__,
        ])
        with self.engine.begin() as conn:
            conn.exec_driver_sql(AUDIT_LOGS_DDL)
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        self.service = ImageGenerationJobQueueService(self.db, media_root=Path(tempfile.mkdtemp()))
        company = Company(
            name="회사 A", business_number="111-11-11111", ceo="테스트", phone="02-000-0000",
            email="a@test.com", address="서울",
        )
        self.db.add(company)
        self.db.commit()
        self.company_id = company.id
        self.data = png(page([700, 500, 900, 600, 800, 450, 1000, 520]))

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def _upload(self, mode=None):
        kwargs = {"mode": mode} if mode is not None else {}
        return self.service.upload_and_split_long_detail_image(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=1, image_bytes=self.data,
            original_filename="long.png", segment_height=1200, requested_by=1, company_id=self.company_id,
            **kwargs,
        )

    def test_the_default_mode_is_still_the_fixed_height_split(self):
        _original, segments = self._upload()
        heights = [a.height for a in segments]
        self.assertTrue(all(h == 1200 for h in heights[:-1]))

    def test_section_mode_cuts_only_in_blank_bands(self):
        original, segments = self._upload("SECTION")
        self.assertEqual(original.purpose, "DETAIL_SOURCE")
        self.assertGreaterEqual(len(segments), 5)
        y = 0
        scaled = Image.open(io.BytesIO(self.data)).convert("RGB")
        for asset in segments[:-1]:
            y += asset.height
            self.assertTrue(row_is_blank(scaled, y), f"y={y}")
        for asset in segments:
            self.assertEqual(asset.purpose, "DETAIL")
            self.assertEqual(asset.source_asset_id, original.id)
            self.assertEqual(asset.rights_status, "RIGHTS_UNVERIFIED")

    def test_resplit_an_existing_original_by_sections(self):
        original, fixed = self._upload()
        created = self.service.split_long_image(
            source_asset_id=original.id, segment_height=1200, requested_by=1,
            company_id=self.company_id, mode="SECTION",
        )
        self.assertTrue(created)
        self.assertEqual({a.source_asset_id for a in created}, {original.id})
        self.assertNotEqual([a.height for a in created], [a.height for a in fixed])

    def test_an_unknown_mode_is_rejected(self):
        with self.assertRaises(BadRequestException):
            self._upload("DIAGONAL")


class RouteSchemaTests(unittest.TestCase):

    def test_request_schemas_default_to_the_fixed_mode_and_accept_section(self):
        from app.domains.media_asset import schema

        self.assertEqual(schema.ImageSplitRequestSchema(source_asset_id=1).mode, "FIXED")
        self.assertEqual(schema.ImageSplitRequestSchema(source_asset_id=1, mode="SECTION").mode, "SECTION")
        self.assertEqual(
            schema.LongDetailImageUploadRequest(owner_type="PRODUCT_CANDIDATE", owner_id=1, image_base64="AA").mode,
            "FIXED",
        )
        with self.assertRaises(Exception):
            schema.ImageSplitRequestSchema(source_asset_id=1, mode="NOPE")

    def test_the_url_import_uses_the_section_mode(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "domains" / "product_import" / "service.py").read_text(encoding="utf-8")
        self.assertIn('mode="SECTION"', source)


if __name__ == "__main__":
    unittest.main()
