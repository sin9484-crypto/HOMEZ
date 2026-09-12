# HOMEZ V8 Visual Commerce Intelligence Design

Date: 2026-09-12
Status: OFFICIAL_V8_GOAL_DESIGN_ONLY
Source video: https://www.youtube.com/watch?v=GECzkEh-bNQ

## 1. Purpose

Turn supplier pages, product images, reviews, HOMEZ product facts, and marketplace
drafts into one evidence-based workflow:

`discover -> collect evidence -> read images -> find conflicts -> propose edits -> approve -> publish`

This is not a generic image generator and not an unrestricted browser bot. Its
primary value is deciding whether an image needs work, explaining why, and
preventing incorrect product information from reaching a marketplace.

## 2. Five-pass video analysis

### Pass 1: End-to-end workflow

The important pattern is not any single AI feature. The video joins review
collection, insight extraction, detail-page diagnosis, content improvement, and
admin-page registration into one continuous task. HOMEZ should reproduce the
workflow contract, not copy the demonstrated product.

Application:

- One product workspace holding source facts, reviews, images, findings, edits,
  approvals, and publishing results.
- Every downstream output points back to its source evidence.
- A resumable state machine replaces a long, opaque agent session.

### Pass 2: Collection and evidence

Large-volume review collection is useful only when provenance and completeness
are known. A raw count such as 4,000 does not prove that the sample is complete,
representative, or permitted by the source.

Application:

- Official API first; approved page reading second; no bypass of CAPTCHA,
  authentication, rate limits, or access controls.
- Store source URL, collection time, method, page range, observed total, collected
  total, deduplication key, and content hash.
- Separate product reviews from seller, delivery, and packaging complaints.
- Detect duplicate, near-duplicate, incentivized, and suspicious review groups.
- Mark partial or stale collections explicitly instead of silently treating them
  as a complete dataset.

### Pass 3: Review intelligence and discovery

Review summaries become commercially useful when they affect candidate selection
and listing content, rather than living as isolated prose.

Application:

- Extract purchase motives, recurring complaints, questions, misunderstood
  dimensions, option-specific issues, return signals, and desired improvements.
- Score evidence strength using frequency, recency, rating distribution, source
  diversity, and confidence.
- Add `content_preparation_cost` and `issue_resolution_potential` to product
  discovery. A promising product that needs excessive correction should rank
  below an equally profitable, registration-ready product.
- Generate testable content hypotheses such as: "show a hand-scale comparison
  because size misunderstanding appears in 18% of relevant reviews."

### Pass 4: Image reading and editing

The key capability is deciding what needs editing before generating anything.

Application:

- OCR text with bounding boxes and confidence.
- Detect product, packaging, logos, badges, tables, dimensions, option labels,
  watermarks, low resolution, cropping, low contrast, and dense text.
- Compare image claims with supplier facts, selected variants, HOMEZ facts, review
  findings, and marketplace draft values.
- Classify each finding as `TECHNICAL`, `FACT_CONFLICT`, `MISSING_EXPLANATION`,
  `UNSUPPORTED_CLAIM`, `DUPLICATE`, or `ACCESSIBILITY`.
- Produce operations, not only prose: crop, brighten, remove background, replace
  text, add comparison panel, generate a new supporting scene, or block use.
- Preserve the original. Show before/after, changed regions, changed facts, and
  the evidence behind every proposal.

### Pass 5: Registration automation and safety

Browser form automation can extend HOMEZ to channels without a usable API, but it
is less reliable than an official API and must not become the system of record.

Application:

- Use official marketplace APIs first.
- Browser assistance may prepare fields and upload approved assets.
- Final submit is a separate command with a fresh preview and approval.
- CAPTCHA, OTP, unexpected price, changed terms, unknown response, or DOM drift
  stops the run and asks the user.
- Persist field-level input, screenshot/evidence, response status, external ID,
  and reconciliation result.
- Never infer success from a toast or button click alone.

## 3. Current HOMEZ fit

### Reuse

- `product_candidate`: candidate, evidence, decision, and selection records.
- `trend_discovery`: external demand signals.
- `media_asset`: URL import, source tracking, rights evidence, validation,
  processing, background removal, composition, and detail-page generation.
- `marketplace_listing`: listing wizard, marketplace precheck, approval package,
  media binding, and submission reconciliation.
- `pricing`: expected margin and variance.
- `ai_governance`: proposal and human-review boundaries.

### Missing capability

- Review-source adapter and review evidence ledger.
- OCR and visual-observation provider contract.
- Cross-source fact graph and conflict detector.
- Evidence-linked image findings and edit proposals.
- Registration-readiness scoring.
- Generic browser-assisted publisher for channels without suitable APIs.
- Evaluation dataset measuring OCR, conflict detection, edit usefulness, and
  publishing reliability.

## 4. Proposed architecture

### 4.1 New bounded context: `visual_commerce_intelligence`

Keep analysis separate from `media_asset`. `media_asset` owns files and
transformations; the new domain owns observations, claims, findings, proposals,
and evaluation.

Core entities:

- `SourceCapture`: immutable capture metadata and content hash.
- `ReviewEvidence`: normalized review facts without silently overwriting source.
- `VisualObservation`: OCR/object/layout observation with bounding box and
  confidence.
- `ProductClaim`: normalized claim, value, unit, source, and observed time.
- `ClaimConflict`: conflicting claims and severity.
- `VisualFinding`: one actionable image problem with evidence links.
- `EditProposal`: ordered edit operations, preview asset, model/provider/version,
  and status.
- `ReadinessAssessment`: versioned score and blocking reasons.
- `PublicationRun`: API or browser-assisted publication attempt and reconciliation.

### 4.2 Provider contracts

- `ReviewSourceProvider.collect(request) -> ReviewCollectionResult`
- `PageEvidenceProvider.capture(url) -> PageCaptureResult`
- `VisualReader.analyze(asset) -> VisualAnalysisResult`
- `ClaimNormalizer.normalize(observations) -> ProductClaim[]`
- `VisualEditor.propose(context) -> EditProposalResult`
- `ChannelPublisher.prepare(package) -> PublicationPreview`
- `ChannelPublisher.submit(approved_preview) -> PublicationResult`

All contracts return `CONFIRMED`, `PARTIAL`, `UNKNOWN`, or `FAILED` outcomes and
must retain provider/version/time metadata.

### 4.3 Fact hierarchy

When sources disagree, HOMEZ must not let an AI choose facts freely.

1. User-confirmed product fact
2. Current supplier structured data
3. Official manufacturer material
4. Existing approved HOMEZ fact
5. Supplier image OCR
6. Marketplace/review inference
7. Generated content

Lower-ranked evidence may open a finding but cannot overwrite a higher-ranked
fact automatically.

## 5. User workflow

### Discovery

1. User supplies a URL, API result, or supplier product.
2. HOMEZ captures product text, images, variants, and available reviews.
3. Duplicate products and duplicate images are grouped.
4. HOMEZ calculates demand, margin, supplier risk, review opportunity, and content
   preparation cost.
5. Candidate screen explains the score and shows blocking unknowns.

### Visual review

1. Images appear in a filmstrip with `usable`, `edit recommended`, or `blocked`.
2. Selecting an image overlays OCR boxes and finding markers.
3. A comparison panel shows image claim, HOMEZ fact, supplier fact, and listing
   value.
4. User accepts, rejects, or edits each factual correction.
5. HOMEZ generates only the approved proposal and preserves the original.

### Listing

1. Approved assets and confirmed facts flow into the existing Listing Wizard.
2. Precheck blocks unresolved critical conflicts.
3. Preview shows every changed field and image.
4. User approves the immutable package fingerprint.
5. API submission or browser-assisted preparation runs.
6. HOMEZ verifies the external listing ID/status before declaring success.

## 6. Readiness score

Display percentages to the user, but store component evidence separately.

- Facts confirmed: 25%
- Image technical quality: 15%
- Image/listing consistency: 20%
- Review questions addressed: 10%
- Variant completeness: 10%
- Marketplace compliance: 10%
- Margin and fulfillment confidence: 10%

Hard blockers override the numeric score: unresolved quantity/origin/composition
conflict, denied asset, missing required option, prohibited claim, or unknown
submission result.

## 7. Automation policy

### Automatic

- OCR and visual observations
- duplicate grouping
- technical diagnostics
- evidence comparison
- reversible preview generation
- readiness recalculation

### User approval required

- changing a product fact
- adding efficacy, safety, certification, origin, composition, or quantified claims
- generating a scene that depicts product use not present in source evidence
- replacing supplier wording
- selecting an image for publication
- marketplace submission

### Always blocked

- inventing dimensions, ingredients, certifications, origin, stock, or options
- removing a mark in order to misrepresent ownership or origin
- bypassing access controls or marketplace verification
- retrying a financially or externally consequential action whose result is
  unknown

## 8. Delivery plan

### Phase A: Evidence foundation

- SourceCapture, ProductClaim, and immutable evidence links.
- One official/approved review source adapter and fixture provider.
- Collection completeness and duplicate metrics.

Exit: a candidate can show exactly where each extracted fact came from.

### Phase B: Image reading MVP

- OCR boxes, basic visual quality checks, structured observations.
- Cross-check quantity, dimensions, origin, material, and option labels.
- Findings UI on existing media assets.

Exit: a controlled benchmark measures extraction accuracy, and no observation is
stored as a confirmed fact without evidence/status.

### Phase C: Review intelligence

- Aspect clustering, product/seller/delivery separation, variant linkage.
- Evidence-backed content opportunities and preparation-cost score.

Exit: reviewers can trace every insight to normalized source records.

### Phase D: Edit proposals

- Operation plan, before/after comparison, approval, versioning, rollback.
- Reuse existing Fabric/background-removal/detail-page capabilities.

Exit: editing never destroys the source and factual changes cannot auto-publish.

### Phase E: Listing integration

- Readiness assessment in Listing Wizard.
- Critical-conflict precheck and approved asset handoff.
- Existing API submission path remains primary.

Exit: an approved synthetic product completes discovery through listing preview
without duplicate data entry.

### Phase F: Browser-assisted channel adapter

- Start with one low-risk channel and `prepare only` mode.
- Stable selectors, page-version detection, screenshots, resumable checkpoints,
  and explicit submit approval.

Exit: repeated isolated runs succeed, DOM drift fails closed, and success is
verified from an external identifier/status rather than UI appearance.

## 9. Evaluation

- OCR field exact-match and character error rate.
- Claim-conflict precision/recall, with critical false-negative count shown
  separately.
- Duplicate-image precision/recall.
- Human acceptance rate for findings and edit proposals.
- Time from candidate to approved listing package.
- Listing rejection/error rate before and after adoption.
- Browser task completion, unknown-outcome rate, and manual recovery time.
- Incremental API/model cost per approved product.

## 10. Product decision

Do not add this entire design to V7. V7 should remain the operationally stable
semi-automation baseline. Build this as a V8 capability, starting with evidence
and image reading. Browser-assisted publishing comes last, after the evidence,
approval, and reconciliation contracts have proved reliable.

## 11. Official goal record

User decision recorded on 2026-09-12:

- Keep V7 as the stable semi-automation baseline.
- Make evidence-based product discovery and visual commerce intelligence a core
  V8 goal.
- Use image reading to decide whether editing is necessary before creating or
  changing an image.
- Apply the same evidence to both candidate selection and listing preparation.
- The target outcome is faster product discovery, fewer listing-data conflicts,
  less unnecessary editing, and safer marketplace registration.
- This record authorizes the goal and design direction only. It does not by
  itself authorize production DB migrations, external writes, marketplace
  submissions, purchases, or payments.
