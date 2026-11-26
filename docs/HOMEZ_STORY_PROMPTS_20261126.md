# 상황 스토리 그림 지시문(프롬프트) 정리 — 레이펄스 워터리스 바디워시 200ml (2026-11-26)

- 사용자 지시: "이미지를 만들수있게 5개 모두 스토리로 만들어라 그리고 프롬프트로 정리해서 이미지 생성까지 맞춰라"
- 이 문서는 **AI에 실제로 보낸 글 그대로**(영어 원문)와 그 뜻, 상품을 붙이는 단계, 스토리 사진에 넣는 문구를 한 곳에 맞춰 둔다.
  코드의 원본은 `app/domains/media_asset/ai_background_concepts.py`(장면 글·공통 규칙)와 `story_scenes.py`(순서·문구)이며, 앱의 '상황 스토리 만들기' 칸에서도 장면마다 같은 지시문을 펼쳐 볼 수 있다.

## 1. 만드는 순서(장면 한 장)

| 단계 | 하는 일 | 어디서 | 바깥 호출 |
|---|---|---|---|
| 1 | 공급처 네 병 사진(#1)에서 파는 한 병만 떼어 냄 → 누끼 #171 | PC 안(`item_isolation`) | 없음 |
| 2 | 흰 뚜껑 아래 몸통(상품 높이 25~100%)이 장면을 비추게 → 누끼 #180 | PC 안(`clear_container`) | 없음 |
| 3 | 상품 없는 빈 장면을 그림(아래 지시문, 장면마다 한 번, 저장해 다시 씀) | 클라우드플레어(무료 한도) | 장면당 1회(허용 필요) |
| 4 | 장면 위에 #180을 바닥선(높이 86%)에 세움: 최대 1.4배·높이 50% 한도, 장면 빛 색·왼쪽 빛 방향 맞춤, 밑면마다 닿는 그림자 + 오른쪽 옅은 그림자 | PC 안(`composition` 바닥 세우기) | 없음 |
| 5 | 상품 보존 검사(빛 맞춤 계산을 다시 해 상품 픽셀이 같은지)·배경 대비 검사 | PC 안 | 없음 |
| 6 | 다섯 장을 이야기 순서로 이어 붙이고 문구를 넣은 스토리 사진(상세 이미지, 가로 780) | PC 안(`story_scenes`) | 없음 |

## 2. 공통 규칙(모든 장면 글 끝에 붙음, 영어 원문)

```
Empty background for a product photo. Composition: close-up tabletop shot, camera just above the table, looking almost straight ahead; the empty flat surface fills the whole bottom 40% of the image edge to edge and runs past the bottom edge, so its front edge is not visible; its far edge is a straight horizontal line at about 60% of the image height; the middle of this empty surface is completely clear so a product can be placed there later; everything behind the surface is far away, small and very strongly blurred. Look: bright, airy, cozy home lifestyle photo; warm morning sunlight from the left; soft shadows; warm beige, cream and light oak palette; warm white balance. No products, no people, no hands, no animals. No text, no letters, no logos, no labels, no watermarks. Photorealistic, square composition.
```

뜻: 상품 사진용 빈 배경. 탁자 높이 바로 위에서 거의 정면으로 본 가까운 탁자. 빈 면이 아래 40%를 가득 채우고 앞모서리는 화면 밖, 먼 모서리는 높이 60%의 곧은 가로선, 가운데는 상품 자리로 완전히 비움. 뒤쪽은 멀고 작고 아주 흐리게. 밝고 아늑한 생활 사진, 왼쪽에서 드는 따뜻한 아침 햇빛, 부드러운 그림자, 베이지·크림·밝은 원목 색. 상품·사람·손·동물·글자·로고·표식 없음. 실제 사진처럼, 정사각형.

## 3. 장면별(이야기 순서)

| 순서 | 장면 | 장면 글(영어 원문, 공통 규칙 앞에 붙음) | 뜻 | 스토리 제목 / 설명 | 결과 사진 |
|---|---|---|---|---|---|
| 1 | 수건 스튜디오(사용법) | A minimal bright photo studio: a smooth light gray table top in the foreground, a neatly folded white cotton towel on its left side, a plain warm white backdrop. | 밝고 단정한 촬영장, 앞쪽 매끈한 밝은 회색 탁자, 왼쪽에 반듯하게 접힌 흰 면 수건, 뒤는 따뜻한 흰 벽 | 뿌리고, 닦으면 끝 / 물 없이 피부에 뿌린 뒤 수건으로 닦아 내세요 | #181 |
| 2 | 간병·침대 곁 | A calm bright bedroom for home care: a light oak bedside table top in the foreground, a folded white washcloth on its left side, a bed with white linen far behind. | 집에서 돌보는 조용하고 밝은 침실, 앞쪽 밝은 원목 협탁, 왼쪽에 접힌 흰 손수건, 멀리 흰 침구의 침대 | 샤워가 어려운 날, 침대 곁에서 / 물을 받지 않고도 닦아 줄 수 있어요 | #182 |
| 3 | 운동 뒤 | A clean bright gym locker room: a light wooden bench top in the foreground, a folded white sports towel on its left side, lockers far behind. | 깨끗하고 밝은 운동 시설 탈의실, 앞쪽 밝은 나무 벤치, 왼쪽에 접힌 흰 운동 수건, 멀리 사물함 | 운동 뒤 샤워실이 없을 때 / 땀을 닦고 끈적임 없이 산뜻하게 | #183 |
| 4 | 캠핑 테이블 | A sunny forest campsite: a light wooden camping table top in the foreground, a folded gray sports towel on its left side, a tent and green trees far behind. | 햇살 드는 숲속 캠핑장, 앞쪽 밝은 나무 캠핑 탁자, 왼쪽에 접힌 회색 운동 수건, 멀리 텐트와 나무 | 씻을 곳이 마땅치 않은 캠핑장에서 / 캠핑·차박 가방에 하나 챙겨 두세요 | #184 |
| 5 | 여행 숙소 | A bright hotel room: a light wooden desk top in the foreground, a small folded white towel on its left side, an open suitcase and a window far behind. | 밝은 숙소 방, 앞쪽 밝은 나무 책상, 왼쪽에 작게 접힌 흰 수건, 멀리 열린 여행 가방과 창 | 여행·출장 숙소에서 / 물 없이 간편하게 마무리하세요 | #185 |

- 수건은 사용법(뿌리고 수건으로 닦기)을 알리는 배경 소품이며, 함께 오는 물건으로 오해되지 않게 상품과 떨어진 왼쪽에만 둔다.
- 스토리 문구는 상품정보제공고시·용기 표기의 사실 범위다. 그림에 새기는 글이라 의료·효능·과장 표현(주장 점검기 목록)은 넣을 수 없다.
- 배경 파일: `storage/media/ai_backgrounds/1/{장면}/v1.png`(2026-11-26 그림, 허용 #46).

## 4. 스토리 사진

- 머리: 상품명(비우면) + "이럴 때 쓰세요".
- 장면마다: "순서. 제목" → 설명 → 장면 사진(600×600) — 위 표 순서 그대로.
- 결과: 아래 '만든 결과'에 적는다.

## 5. 만든 결과(2026-11-26, 운영 DB, 바깥 호출 0)

- 사용자 "5개 모두 스토리로" → 백업 `Homez-Backups/pre_story_page_20261126_200404`(무결성 ok) 뒤 `StoryService.run(회사 1, 사용자 1, 누끼 #180, 다섯 장면 모두)`.
- 장면 사진: #181 수건 스튜디오 · #182 간병·침대 곁 · #183 운동 뒤 · #184 캠핑 테이블 · #185 여행 숙소(모두 같은 내용이 이미 있어 그대로 씀, 배경 v1 재사용).
- **스토리 사진 #186**(상세 이미지, 780×3,754): 머리 "레이펄스 워터리스 바디워시 200ml / 이럴 때 쓰세요" → 1~5 장면, 위 표의 제목·설명 그대로.
