# Zaminex Investigation Log

Per-stage root-cause evidence. Essential findings are also reproduced in each
stage report.

## Open items (carry-over)

- **Stage 7 residual (user-side, non-blocking):** the map-picker variant ladder
  was only verified against MOCK payloads (Nominatim is unreachable from this
  sandbox). On a real-internet machine, pick a real Mazandaran district that
  exists in OSM and confirm the camera lands on it, then record the result
  here. One-liner for the report font (Stage 9) is kept below.
- **Stage 9 follow-ups (owner decision, future):** (1) if a second consumer
  appears, promote AI data assembly to a shared module (e.g.
  `apps/analytics/data.py`) instead of importing `_property_ai_data` from
  `apps/analytics/views`; (2) if the owner wants the richer listing/follow-up
  columns back in the PDF (start+end date, probability/type/contact), that is a
  trivial follow-up — Stage 9 standardized to the requested column spec.
- **Stage 13 follow-up:** appraisal download on a non-owned property is 403 by
  design (sensitive). Future: hide the appraisal section for non-owners rather
  than show a 403 toast.
- **Stage 14 follow-up:** the model ``*_display`` choice labels are still
  English (``Property.Status.AVAILABLE = ("AVAILABLE", "Available")``, and so
  on for Listing/Task/FollowUp). The activity feed now maps them to Persian via
  ``apps/activity/labels.py``, but the raw labels remain a pre-existing UI
  contract the frontend also maps around. Promoting the labels themselves to
  Persian is a UI-wide change (forms, admin, serializers) — left for a future
  stage; do not do it opportunistically here.

## Stage 14 — activity-log status-change entries must be fully Persian

### Token audit (evidence-first)

Every ``log_*`` description builder in `apps/activity/signals.py` (post-refactor)
plus the render sites:

| site | interpolated token | class | action |
|---|---|---|---|
| `log_property_save` status-change | `old_status` + `instance.status` (raw codes) | **raw-English label → Persian** | fixed |
| `log_listing_save` status-change | `instance.get_status_display()` ("Active", "Sold", …) | **English label → Persian** | fixed |
| property/listing create/update | `title`, `internal_code` | data value → keep | none |
| `log_task_save` → `build_description` | `STATUS_FA`/`PRIORITY_FA`/`TYPE_FA` | already Persian | verified |
| `log_followup_save` status/archive | «تکمیل شد»/«ویرایش شد»/«بایگانی شد» | already Persian | verified |
| `log_followup_save` create | `follow_up_type` (metadata only) | data value → keep | none |
| `log_consultant_save` | `full_name`, «فعال/غیرفعال» | data value / already Persian | none |
| appraisal save/delete | `original_filename`, `property.title` | data value → keep | none |
| `reports/views.py` exports | `title` | data value → keep | none |
| `tickets/services.py` | `TicketAuditAction.choices` | already Persian | out of scope |

`cache_old_property_status` stores `old.status` (the raw code) in `pre_save`;
`log_property_save` then interpolates it raw — that is the property bug. The
listing bug is `get_status_display()` returning the English label. Task and
follow-up descriptions were already Persian.

### Per-model choice-label evidence

All four vocabularies' ``*_display`` labels are **English** (Property
"Available/Reserved/Sold/Archived"; Listing "Draft/Active/Paused/Sold/Expired/
Archived"; Task "Pending/…"/"Low/…"/"Viewing/…"; FollowUp "Call/…"/
"Scheduled/Completed"). Decision: **do NOT change the labels** — UI blast
radius (frontend maps them itself in `shared/lib/utils.ts`, and forms/admin/
serializers read them). Use a display-time Persian map; flagged as a future
stage (Open items).

### Fix (minimal diff)

- New `apps/activity/labels.py` — single source of Persian labels (mirrored
  from the frontend). ``_choice_tokens`` derives the token set from each
  model's ``TextChoices`` (code **and** label → Persian) so the recognised set
  cannot drift from the schema. ``status_label`` for writing;
  ``translate_description`` for rendering (word-boundary regex; unknown values
  pass through untouched).
- `apps/activity/signals.py` — property status-change interpolates
  `status_label("property", old/new)`; listing status-change interpolates
  `status_label("listing", instance.status)` instead of `get_status_display()`.
- Render sites — `apps/activity/views.py` list endpoint wraps `log.description`
  in `translate_description(log.description, log.target_type)`; `pdf.py`
  `_logs_section` does the same via `_translated_log_description` before `t()`.
- No frontend change — `ActivityLogPage.tsx` renders `act.description` verbatim,
  so the fix is server-side.

### Verification

11 new tests (4 new-row, 2 legacy-row via list endpoint, 4 unit, 1 PDF
story-level `assertNotIn` raw ASCII — `t()` passes ASCII through, so a failed
translation leaves it literal). Full suite **697 tests, 0 failures** (was 686).
`npm run build` OK — bundle `main-Bn6E-9Vl.js`.

## Stage 9 — property PDF: empty-failure hardening + AI section + log tables

### Diagnosis (evidence-first)

The committed font `ZaminexB/static/fonts/ttf/IRAN Rounded.ttf` is intact in this
checkout (sha256 `1fd361ec…b39` matches), and `build_property_pdf` renders a valid
multi-page PDF. The user's empty PDF is therefore most consistent with a
corrupted/missing font **in their clone** — the repo has no `.gitattributes`, so
Git's text handling can CRLF-convert or truncate binary files on checkout/clone.
Simulated each failure mode against the current code (`_register_font` +
`build_property_pdf`):

| simulated corruption | current behaviour |
|---|---|
| missing file | `TTFError: Can't open file …` (uncaught → 500 HTML, no Persian detail) |
| CRLF-converted (git text handling) | `TTFError: Corrupt TTF file … cannot read Table Directory` |
| bit-flipped header | `TTFError: Not a recognized TrueType font` |
| truncated 1 KB | `unpack requires a buffer of 2 bytes` |
| empty / ASCII-text file | `TTFError: … is not a TTF file` |

Every corruption raised an opaque `TTFError` mid-render — never a blank PDF, but
an uncaught 500 with an English exception and no Persian detail, exactly the
failure the user hit. The hardening converts all of these into one clean 500
with a Persian message (see Fix).

### Root cause

`_register_font` called `pdfmetrics.registerFont(TTFont(...))` with no validation
and no error handling, so a missing/corrupt font (the only font the PDF uses)
raised a raw reportlab `TTFError` during `doc.build` — a 500 with an English
message, no Persian detail, and no protection against a half-rendered/blank page.

### Fix (minimal diff, `apps/reports/pdf.py` + `.gitattributes`)

- **`.gitattributes`** (new, repo root): `*.ttf *.otf *.eot *.woff *.woff2 *.png
  *.jpg *.jpeg *.gif *.ico *.pdf *.zip` marked `binary` so future clones can't
  corrupt them via text handling.
- **`_register_font`**: reads the first 4 bytes and rejects anything without a
  TTF/OTF/TTC magic number; wraps the whole load in `try/except` and re-raises a
  new `_FontUnavailable` (DRF `APIException`, `status_code=500`,
  `code="report_font_unavailable"`, Persian detail «فایل فونت فارسی گزارش در
  دسترس نیست یا خراب است؛ امکان تولید PDF وجود ندارد.»). Registration stays
  lazy (still only on first export).
- **AI section** (`_ai_section`): reuses `apps.analytics.views._property_ai_data`
  + `apps.analytics.ai_service.get_cached_description`, wrapped so any `Exception`
  (unconfigured, provider failure, timeout) omits the section. Renders
  summary + positives + negatives with the existing `t()`/`fa_number`/styles.
- **Tasks section** (`_tasks_section`, new): عنوان / وضعیت / تاریخ سررسید with
  `TASK_STATUS_FA` labels. Listings table standardized to عنوان/کانال/قیمت/وضعیت/تاریخ,
  follow-ups to عنوان/وضعیت/تاریخ زمان‌بندی (per the requested column standard).
  Section order is now info → KPIs → AI → listings → tasks → follow-ups →
  charts → logs (۱–۸).

### Verification

- Failure-mode capture above; after fix, missing and CRLF-corrupted fonts both
  return 500 with `code="report_font_unavailable"` and the Persian detail (never
  `%PDF-`).
- New tests (8, in `apps/reports/tests.py`): `PropertyPdfContentTests`
  (populated → 200, valid, ≥2 pages, >10 KB), `PropertyPdfEmptyHistoryTests`
  (empty → 200 valid + all four entity-table placeholders asserted at story
  level), `PropertyPdfAiSectionTests` (omitted when unconfigured, present when
  mocked — size delta at build level, failure swallowed → still 200),
  `PropertyPdfFontFailureTests` (missing/corrupt → 500 + Persian detail).
- Full Django suite: **669 tests, 0 failures** (was 661; +8).
- Font integrity one-liner for the user's machine:
  `sha256sum "ZaminexB/static/fonts/ttf/IRAN Rounded.ttf"` — expect
  `1fd361ec4e71e27bbcbc315dbad80aec783c02be027c22eaab969703485e0b39`.

## Stage 10 — consultants can report on own AND shared properties

### Diagnosis (evidence-first)

Two access rules coexisted for the property report. Reproduced directly:
`can_access_property(B, P2) == True` (the canonical rule — shared ⇒ accessible)
while `get_property_for_user_or_403(B, P2)` raised `PermissionDenied` (its own
ad-hoc `consultant_id == user.pk` check ignored `is_shared`).

Caller enumeration of `get_property_for_user_or_403` (grep across the repo):
1. `apps/reports/views.py:48` — inside `PropertyReportView._report()`, which
   serves **both** the JSON endpoint (`PropertyReportView.get`) and the CSV
   endpoint (`PropertyReportExportView.get`, which instantiates the view and
   calls `view._report`).
2. `apps/reports/tests.py` — unit test usages only.

Every caller is report-context and should receive the shared-access rule. The
PDF endpoint (`PropertyReportPdfView`) does **not** use the helper — it already
calls `can_access_property` directly, which is exactly the rule the helper now
delegates to.

### Root cause

`get_property_for_user_or_403` re-implemented its own rule
(`role != ADMIN and consultant_id != user.pk`) instead of delegating to the
canonical `can_access_property`, so JSON + CSV denied shared properties while
PDF allowed them — the three formats disagreed.

### Fix (minimal diff)

- `apps/reports/services.py` — `get_property_for_user_or_403` now delegates to
  `apps.common.access.can_access_property` (admin → any; consultant → own OR
  shared; unauthenticated → denied). One line + docstring. JSON/CSV/PDF now all
  flow through the single canonical rule.
- `PropertyDetail.tsx` — the «مشاهده گزارش کامل» button was un-gated (always
  visible); now gated on `canViewPrivateInfo` (= admin | own | shared, the FE
  mirror of `can_access_property`) so a non-accessible property shows no report
  entry. `PropertyReportsPage.tsx` **already** renders a 403 as a graceful
  error card (`apiErrorMessage` → red card) — no change needed there.

### Verification

- New `PropertyReportAccessMatrixTests` (2 tests): full user×property×format
  matrix (A/B/C × P1/P2/P3 × JSON/CSV/PDF) asserting the expected status AND
  that the three formats agree on every cell; plus admin-sees-all.
- Full Django suite: **671 tests, 0 failures** (was 669; +2).
- Frontend: `npm run build` OK (new bundle `main-f9XSwRwH.js`); vitest 24/24.

## Stage 11 — deleting a property removes its row from the list in the same tick

### Diagnosis (evidence-first) — which mechanism is live

Two candidate mechanisms were examined.

**Mechanism (a) — page-local state never updated on delete — CONFIRMED (sole root cause).**

Data-flow trace (before the fix):

1. The admin list `PropertiesPage` renders rows from its **own** local state
   `serverProperties`, populated by `fetchServerProperties()` (server-side
   pagination: GET `/properties/api/properties/?page=N&page_size=20`). The
   `properties` prop is destructured as `initialProperties` and **never read**
   (grep: only `initialLoading` is used) — the list does not derive from App
   state at all.
2. «حذف» in the row action menu → `setConfirmDelete(id)` → `<ConfirmModal
   onConfirm>` → `onDelete(id)` = App `deleteProperty`.
3. `deleteProperty` DELETEs, toasts success, updates App-level `properties`
   (the combobox list used by wizards/follow-ups/filters), re-fetches it, and
   `setPage("properties")` (a no-op — we are already on that page).
4. `PropertiesPage.serverProperties` is **never touched**, and
   `fetchServerProperties` is **not re-triggered** (its deps — currentPage /
   pageSize / search / filters / propertyTypeRef / attrValues / csrfToken —
   are unchanged). The deleted row therefore stays visible until a manual F5
   remounts the page and re-runs the fetch. Exactly the reported symptom.

**Mechanism (b) — stale HTTP cache re-inserting the row — DISPROVEN (not live).**

Captured the real response headers of the authenticated properties LIST GET
(`django.test.Client`, `HTTP_HOST=localhost`, `force_login(admin)`):

```
status 200
Cache-Control = 'no-store, no-cache, must-revalidate, max-age=0'
Pragma        = 'no-cache'
ETag          = None
Last-Modified = None
Expires       = None
Vary          = 'Accept, Cookie'
```

`SecurityHeadersMiddleware` (`apps/common/middleware.py`) already sets
`Cache-Control: no-store` on every authenticated response, so the browser never
serves a stale list and a deleted row cannot be re-inserted from cache. No
server-side cache defect — **no backend change needed**.

### Fix (minimal diff, frontend only)

- `App.tsx` `deleteProperty` — now returns `true` on success / `false` on
  failure (so the page knows the outcome without an event bus), and reads the
  server's error message via `apiErrorMessage` (was a generic «خطا در حذف ملک»).
  App-level `setProperties` + `fetchProperties` + toast all stay.
- `PropertiesPage.tsx`:
  - `fetchServerProperties` GET now sends `cache: "no-store"` (belt-and-suspenders;
    matches `fetchProperties`).
  - New `applyLocalRemoval(ids)` helper: on confirmed success removes the
    row(s) from `serverProperties` in the same tick, decrements `totalCount`,
    clears the ids from `selected`, then either steps the page back (if the
    whole current page just emptied and `currentPage > 1` → the effect re-fetches
    the now-last page) or re-fetches (no-store) to re-sync shifted rows.
  - Single-row ConfirmModal and the table bulk-delete now `await onDelete(id)`
    and only mutate locally when it returned `true` — a failed delete leaves the
    row untouched (App already toasted the server message).
- `types.ts` — `PropertiesPageProps.onDelete` widened `(id: string) => void` →
  `(id: string) => Promise<boolean>` (supporting type change).

### Pagination edge note

The addenda referenced the tickets list as the reference UX for the step-back,
but **no step-back exists anywhere** — `TicketsPage.tsx` (lines 924–925, 959–1011,
1119) paginates server-side via a plain `<Pagination page={listPage} total={total}
…>` with no clamp, and it has no delete action at all (grep `حذف`/`DELETE` → only
a recipient-removal `X`). The standard step-back was implemented directly here.

### Verification

- Full Django suite: **671 tests, 0 failures** (no backend change).
- Frontend: `npm run build` OK (new bundle `main-DrLSEzf0.js`); vitest 24/24.

## Stage 12 — attribute «دسته‌بندی ویژگی‌ها» (essential / non-essential)

### Classification rule (single source of truth)

`apps/basics/categorization.classify_attribute(is_core, active_binding_count)`:

    essential     ⇔  is_core  OR  active_binding_count ≥ 1
    non_essential ⇔  otherwise

`active_binding_count` = active `PropertyTypeAttribute` + `DealTypeAttribute`
rows (`is_active=True`). The `is_core` clause keeps core attributes (متراژ /
قیمت / …) essential even when they have no binding row — a binding-only rule
would misclassify them.

### Migration (two-step, reversible)

`0003_attribute_category.py`:
1. `AddField category` — `CharField(max_length=20,
   choices=Category.choices, default=NON_ESSENTIAL)`.
2. `RunPython` importing the pure function; iterates every row (the historical
   model's default manager is a plain Manager, so soft-deleted rows are covered
   too) and `save(update_fields=["category"])`. Reverse = `update(category=
   "non_essential")` (safe default; classification re-derivable).

### Dev-DB result (populated, after migrate)

Live attributes: **27** → **26 essential / 1 non-essential**.

- Essential sample: متراژ / تعداد اتاق / طبقه / سال ساخت (core) and
  تعداد کل طبقات / واحد در طبقه / نوع سند / پارکینگ … (actively bound).
- Non-essential (only one): آنتن مرکزی (unbound, non-core).
- Soft-deleted rows: 0.

### API

`AttributeSerializer.fields` gained `"category"` — one-word field kept as
`category` on the wire (consistent with `entity` / `unit`, which are also
exposed un-camelCased). Writable via the existing PATCH, present in the list;
new attributes default to `non_essential` (model default). Additive only:
`AttributeMiniSerializer` / `FormFieldSerializer` (form schema) and the
catalogue are untouched — the full suite still passes, so no list consumer
broke on the extra field.

### Frontend

Third tab «دسته‌بندی ویژگی‌ها» in `AttributesPage.tsx`:
- renders from the shared `attributes` state (no new fetch) — two groups
  «ویژگی‌های ضروری (n)» / «ویژگی‌های غیر ضروری (n)» with fa-IR counts, each
  row = name + a move action (`handleMoveCategory`) that PATCHes `{category}`,
  updates optimistically, reverts + refetches + toasts the server message on
  4xx. Empty group → muted «هیچ ویژگی‌ای در این دسته نیست.» line.
- The other two tabs' content blocks are byte-for-byte unchanged (the tab map
  array only gained a third entry).

### Verification

- 10 new tests in `apps/basics/tests/test_attribute_category.py`
  (`ClassifyAttributeTests` — pure rule; `AttributeCategoryApiTests` — default,
  list, PATCH both directions, invalid category → 400).
- Full Django suite: **681 tests, 0 failures** (was 671; +10). The fresh-DB run
  covers migration-apply.
- `npm run build` OK (bundle `main-DiLqo31d.js`); vitest 24/24 (no new helper
  warranted a vitest test — the grouping is an inline `filter`).

## Stage 13 — other consultants' properties: images render, «جزئیات» hidden, tabs locked

### Diagnostic matrix (evidence-first, dev DB via `django.test.Client`)

Fixtures 17 (consultant B's own property), 18 (consultant A's non-shared
property), 19 (A's shared property) — each with images, an appraisal PDF, and an
avatar on A's profile. Probed as consultant B and as admin with `scope=all`:

| case | LIST JSON `imageUrl` | media GET (B) | media GET (admin) | classification |
|---|---|---|---|---|
| B → own property image | present | 200 | 200 | (d) n/a — works |
| B → A's non-shared image | **present** | **403** | 200 | **(b) media 403** |
| B → A's shared image | present | 200 | 200 | works |
| B → A's appraisal PDF (non-shared) | — | 403 | 200 | sensitive: correct |
| B → A's avatar | — | 403 | 200 | own-only: correct |
| anonymous → any media | — | 403 | — | denied |

The single failure is **(b)**: the serializer already includes `imageUrl` for
every `scope=all` viewer, but the media endpoint returned 403 for a non-owner /
non-shared property image. No (a) serializer-omission, no (c) 404, no (d).

### Root cause

`_can_access_media` (`apps/common/media.py`) gated property images behind
`prop.consultant_id == user.pk or prop.is_shared`. Property images are part of
the property *read* model — the «همه املاک» list and the detail page hand the
same URL to every authenticated consultant — so the media 403 was an
inconsistency between the API payload and the media endpoint.

### Fix (minimal diff)

- `apps/common/media.py`: property-image branch now
  `return PropertyImage.objects.filter(image=rel_path).exists()` — any
  **authenticated** user may load a property image; the existence check still
  blocks arbitrary path guessing. Docstring rewritten with the full boundary.
- **Unchanged byte-for-byte:** appraisal-PDF branch (admin / assigned
  consultant / shared — sensitive documents), consultant-avatar branch (own
  only), admin-avatar branch (never to consultants), and the `_safe_relative_path`
  traversal guard.
- `PropertyDetail.tsx`: new `isOwn = role === "admin" ||
  String(consultantId) === String(currentUserId)`. When NOT `isOwn`:
  consultant-section «جزئیات» button hidden; the three tabs (آگهی‌ها / وظایف /
  پیگیری‌ها) render a centred lock `EmptyState` (mirrors the existing
  "به‌زودی" EmptyState already in the same file) with exact sentences
  «شما به آگهی‌های این ملک دسترسی ندارید» / «شما به وظایف این ملک دسترسی
  ندارید» / «شما به پیگیری‌های این ملک دسترسی ندارید». Gallery and report
  button and appraisal section untouched.

### Verification

- `ProtectedMediaTests` expanded to 10 tests: B→A's image **200**; B→A's
  appraisal PDF **403** (owner/admin 200); B→A's avatar **403** (owner/admin
  200); unknown path 403; owner/admin/shared/anon cases kept.
- Full Django suite: **686 tests, 0 failures** (was 681; +5).
- `npm run build` OK (bundle `main-Bn6E-9Vl.js`).

### Follow-up (flagged, NOT implemented)

Appraisal download on a non-owned property is 403 by design (sensitive). For a
future stage: hide the appraisal section for non-owners rather than show a
403 toast.

## Stage 8 — attribute management: real delete, instant add, consistent lists

### Diagnostic matrix (evidence-first, dev DB via `django.test.Client`)

- **ADD** — POST `/basics/api/attributes/` → 201, body carries `id` (optimistic
  insert works); follow-up GET `?all=1` → 200, new id present (lands at index 0).
  Response headers `Cache-Control: no-store, no-cache, must-revalidate, max-age=0`
  — the attributes CRUD list is **not** in the Phase-5 reference cache.
- **DELETE core** → 400, body `["ویژگی‌های ثابت به ستون‌های پایگاه داده متصل هستند و قابل حذف نیستند."]`.
- **DELETE bound (active binding)** → **204** pre-fix; row soft-deleted
  (`deleted_at` set) while the active `PropertyTypeAttribute` row stayed behind →
  **orphaned binding** (the bug).
- **DELETE unbound non-core** → 204, row soft-deleted and hidden from `?all=1`.
- `POST /basics/api/attributes/{id}/restore/` → 200 (Django admin exposes
  `restore_selected` via `SoftDeleteAdmin`); the React UI has **no** restore path.

### Root cause (delete) — `AttributeViewSet.perform_destroy`

Only core attributes were refused. Bound attributes fell through to `instance.delete()`
(soft-delete), leaving active bindings pointing at a now-hidden attribute that still
rendered on the forms.

### Fix (minimal diff)

- `perform_destroy`: core guard unchanged; added an active-binding guard —
  `PropertyTypeAttribute.objects.filter(attribute=instance, is_active=True).count()
  + DealTypeAttribute.objects.filter(attribute=instance, is_active=True).count()`
  → `400` «این ویژگی به {n} نوع متصل است؛ ابتدا اتصالات را حذف کنید.». Unbound
  non-core still `instance.delete()` (soft-delete — recoverable via restore/admin,
  stored EAV values stay readable).
- `AttributesPage.tsx`: confirm-modal copy for a bound attribute now says the
  links must be removed first (was «با حذف آن، این وصل‌ها نیز برداشته می‌شوند» —
  no longer true). Toast already surfaced the server's exact 4xx message
  (`Array.isArray(data) ? data[0] : data?.detail`), so no toast change was needed.

### Second defect found while greening the suite — `BasicsViewSet.get_queryset`

`BasicsViewSet.get_queryset` returned `self.queryset` (the *class-level* QuerySet)
directly for `?all=1` (no `.filter()` to clone it). Django caches a QuerySet's
results on first evaluation, so the first `?all=1` response was replayed, stale,
for every later request in the process — newly-added/deactivated attributes never
appeared. This was exposed by the new delete test making a `?all=1` call in an
earlier class than `AttributeListingTests`. Fix: `queryset = self.queryset.all()`
(one line, in `apps/basics/views.py`), which also un-stales the property-type /
deal-type / usage / geography lists.

### Cross-check (Phase-5 signal invalidation)

Create attribute → bind to property type → next
`/basics/api/schema/property-form/?propertyType=apartment` returns the new field
(signal invalidation works; verified on the dev DB after the fix).

### Verification

- New Django tests in `apps/basics/tests/test_attribute_admin.py`:
  `AttributeDeleteTests` (core refusal, bound new-guard refusal + count, unbound
  soft-delete, restore) and `AttributeAddThenListTests` (created id in `?all=1`).
- Two pre-existing tests updated to the new bound-delete semantics:
  `test_api.AttributeManagementTests.test_deleting_an_attribute_is_a_soft_delete`
  and `test_a_soft_deleted_attribute_can_be_restored` (now delete an *unbound*
  attribute), and `test_detail_route_visibility.…test_the_same_holds_for_attributes`
  (uses an unbound attribute so the delete leg stays valid).
- Full Django suite: **661 tests, 0 failures** (baseline 655 + 6 new).
- Frontend: `npm run build` OK; vitest 24/24.

## Stage 7 — precise province/city/district zoom (geocoding) + NO-MOVE fallback

### Diagnosis (evidence-first)

Nominatim reachability: `https://nominatim.openstreetmap.org` is **unreachable
from the sandbox** (`curl` → SSL_ERROR_SYSCALL on 443, empty reply on 80) while
general egress (PyPI) works — so REAL-mode walk-through was impossible; MOCK-mode
committed tests used instead.

Suspect classification (code + seed-data analysis):
- **(i) display-name vs OSM-name mismatch / thin coverage — CONFIRMED.** The
  seed tree (مازندران → ساری) exposes محله display names like `بلوار کشاورز`
  (a boulevard, not a neighbourhood), `مرکزی` ("central", generic), `آزادی`
  ("freedom", generic), `میدان ساعت` (a square), and `Daryashahr` (Latin
  script). These are exactly the inputs the resolver sends verbatim, and small
  محلهs have thin/no OSM coverage.
- **(ii) bounded viewbox too tight — CONFIRMED (contributing).** halfLat 1.6 /
  halfLng 2.6 can exclude edge-of-province places; widened to 2.2 / 3.5.
- **(iii) 1 req/s queue + 429 — NOT the primary cause.** The queue enforces
  ≥1.1 s and retries 429 once; only city+district geocode (province is static).
- **(iv) context wiring defect — DISPROVED.** `LocationSelect` disables city
  without province and district without city; `onProvinceChange` resets
  city+district, `onCityChange` resets district. No stale-parent path exists.

### Fix (iranLocations.ts + PropertyMapPicker.tsx)

- **Variant ladder** (`buildQueryVariants`): district → "d c p" → "d p" → "d";
  city → "c p" → "c". Free-text search (no `variants` option) keeps the single
  qualified query, bounded→unbounded, no ladder (today's behaviour).
- **Viewbox** widened to halfLat 2.2 / halfLng 3.5; `bounded=1` dropped when the
  variant is fully qualified (parents already in the query).
- **Acceptance rule** (`acceptsResult`): fully qualified → accept top hit;
  partially qualified → hard-reject only a clear mismatch (address names a
  different known Persian province); missing/English-only address → accept.
- **NO-MOVE fallback**: removed the picker's fly-to-province-centre fallback and
  the resolver's static-city-table fallback; on city/district failure the picker
  shows the short auto-dismissing `searchNoResult` notice and does not move.
  Province still zooms from the static table (no internet).
- **Tests**: added `vitest@^3.2.7` (dev-only) + `npm run test` script +
  `src/shared/lib/iranLocations.test.ts` (24 tests, fetch mocked, deterministic).

### Verification

- vitest: 24/24 pass (variant order, acceptance rule, 429 retry, offline → null,
  search single-query, province static-no-network).
- Django suite: 655/0. `npm run build` OK (bundle `main-BYUBwS0S.js`, CSS
  unchanged). No backend diff; no new runtime deps (vitest is dev-only).

## Environment rebuild (canonical — run verbatim after any sandbox wipe)

A wipe removes `.venv/`, `ZaminexF/node_modules/`, `/home/user/pg/` (PostgreSQL)
and the DB data dir; the repo stays but the local branch may reset to `fc63dbd`.
Recovery order:

1. **Git state** — `git fetch origin arena/01a05300-zaminex2` then
   `git reset --hard FETCH_HEAD` (history is linear; the remote carries every
   pushed stage commit). Preserve any uncommitted work to `/tmp/` first.
2. **Backend venv** — `python3 -m venv .venv` +
   `.venv/bin/pip install -r ZaminexB/requirements.txt`.
3. **PostgreSQL** — `pip download pgserver==0.1.4 --no-deps -d /home/user/pgwheel`;
   `unzip` the wheel, move `pgserver/pginstall` to `/home/user/pg/pginstall`;
   create the missing libpq symlink `ln -s libpq.so.5.16 libpq-084d956f.so.5.16`
   in `pginstall/lib`; fetch `REL_16_2` source and `make USE_PGXS=1
   PG_CONFIG=/home/user/pg/pginstall/bin/pg_config install` in
   `contrib/pg_trgm`; `initdb -D /home/user/pg/data --locale=C.UTF-8
   --encoding=UTF8 -U zaminex`; `pg_ctl -D /home/user/pg/data -o "-p 5432" start`;
   `CREATE DATABASE zaminex` + `ALTER USER zaminex WITH PASSWORD 'zaminex'`;
   restore `zaminex_backup.sql` after stripping the `\restrict`/`\unrestrict`
   lines; `manage.py migrate` + `manage.py check`.
4. **Frontend** — `npm install` in `ZaminexF` (installs the `vitest` devDependency
   added in Stage 7; run `npm run test` for the resolver suite).
5. **Baseline gate** — `manage.py test apps` must be **655/0** before any code
   edit. `export DATABASE_URL=postgres://zaminex:zaminex@127.0.0.1:5432/zaminex`
   and `export LD_LIBRARY_PATH=/home/user/pg/pginstall/lib` first.

## Stage 6 — map picker overlays / centre marker / helper line

### Reproduce / locate

The two overlay chips (lat/lon readout bottom-left, «موقعیت ثبت شد» badge
top-right) are `absolute` with `pointer-events-none` but **no z-index**, so
Leaflet's panes (z 200–700) and controls (z 1000) paint over them — they were
invisible. The confirm button already worked because it carries `z-[1000]`.

### Fix (minimal diff, PropertyMapPicker.tsx only)

- **Chips** — added `z-[1000]` to both (kept `pointer-events-none`).
- **Badge/zoom overlap** — Leaflet's zoom control defaults to `topright`, which
  the badge occupied. Leaflet's `zoomControl` Map option is boolean-only (no
  position object — verified in `leaflet-src.js`), so repositioning would need a
  `<ZoomControl position>` child. Chose the lighter, convention-preserving fix:
  offset the badge left (`right-2` → `right-12`) so it clears the ~30px control,
  keeping zoom at its default `topright` like every other map.
- **Centre marker** — replaced the inline circle `divIcon` with
  `makePinIcon("#0BB68A")` — the same teardrop pin template/size/anchor as the
  located-property markers (`iconSize [34,40]`, `iconAnchor [17,38]`), fixed
  green, so the pin tip lands on the map centre. No change to `position={center}`
  tracking or `CenterTracker`.
- **Helper line** — `flex flex-wrap gap-3` → `flex flex-nowrap items-center
  gap-3 overflow-x-auto whitespace-nowrap`; the three hints + separators are
  byte-identical, no wrap at any width, horizontal scroll at 320px.

### Verification

- Interaction logic (search, province/city/district resolution, confirm flow,
  value/centre sync, all `flyTo`) byte-identical — diff shows presentation only.
- Both create + edit forms share the single `PropertyMapPicker` (grep: two
  imports, one definition, no fork).
- Full suite 655 tests, 0 failures. `npm run build` OK.

## Stage 5 — consultant legend under admin map (scalability)

### Reproduce / locate

The admin «نقشه توزیع املاک» legend rendered `consultantColorLegend` rows as a
single `flex flex-wrap` row of chips with no bound or truncation: with 60+
consultants the legend became an unbounded wall, and long consultant names
pushed rows out of line.

### Fix (minimal diff, AdminDashboard.tsx only)

Chose **pattern (a) — bounded scrollable grid** (rejected (b) collapsible: it
hides the majority of a *reference* legend behind a click by default, and its
expanded state reintroduces an unbounded wall unless also bounded — i.e. (a)
plus a toggle). Layout container `flex flex-wrap` → `grid grid-cols-2
lg:grid-cols-3 gap-x-4 gap-y-1.5 max-h-36 overflow-y-auto rounded-lg border
border-border p-2`. Row anatomy preserved (dot + name + fa-IR count); added
`min-w-0` on the row, `min-w-0 truncate` + `title` on the name, `flex-shrink-0`
on dot/count so names truncate instead of overflow. Data logic
(`consultantColorLegend` useMemo) byte-identical; empty state untouched.

### Verification

- `git diff` shows only the legend container + row classes; `consultantColorLegend`
  useMemo unchanged (diff evidence).
- Full suite 655 tests, 0 failures. `npm run build` OK — bundle `main-DkmEwv2r.js`
  + CSS `main-B0kUzkT2.css` (new Tailwind utilities from the added classes).

## Stage 4 — maps default to Mazandaran (Bug 3)

### Reproduce / locate

Grep for `IRAN_DEFAULT_*` across `ZaminexF/src` found the default view constant
`IRAN_DEFAULT_CENTER = [35.6892, 53.0]` / `IRAN_DEFAULT_ZOOM = 5` (Tehran area,
country zoom) in `shared/lib/iranLocations.ts`. Three consumers of the constant:

1. `PropertyMapPicker` (create + edit) — CENTER (3×) + ZOOM (2×).
2. `PropertyDistributionMap` (dashboard) — CENTER (empty-points fallback) + a
   hardcoded `5` zoom literal in the same fallback.
3. `PropertyLocationsMap` (consultant) — CENTER (empty-points fallback) + the
   same hardcoded `5` zoom literal.

`PropertyLocationMap` (property detail) does **not** use the constant — it
centres on the property coordinates (`zoom=15`) and returns `null` when the
property has no coordinates, so it is a fly-to consumer, not a default-view one.

### Fix (minimal diff)

- Renamed `IRAN_DEFAULT_CENTER` → `DEFAULT_VIEW_CENTER` = `[36.4, 53.2]` and
  `IRAN_DEFAULT_ZOOM` → `DEFAULT_VIEW_ZOOM` = `8` (honest names + new values).
- Updated all three consumers; replaced the two hardcoded `5` empty-fallback
  zoom literals with `DEFAULT_VIEW_ZOOM` so the empty dashboard/consultant maps
  now show Mazandaran at the same zoom as the picker.
- Fly-to zooms (16/15/12/9/13/8 for real coordinates) untouched.

### Zoom math

Mazandaran province bounds used for the midpoint: **lat 35.9–36.9, lng
52.1–54.4** (latitude span ≈ 1.0°, longitude span ≈ 2.3°). Centre `[36.4, 53.2]`
is the exact midpoint: (35.9+36.9)/2 = 36.4 and (52.1+54.4)/2 = 53.25 ≈ 53.2.

Smallest surface is the picker panel (h-64 ≈ 256px). Visible latitude span at
zoom z ≈ 360 / 2^z → z=8 ≈ 1.4°, z=9 ≈ 0.7°. The province's ≈1.0° latitude span
fits z=8 with margin; z=9 would crop it.

### Verification

- `grep IRAN_DEFAULT` → 0 hits; all consumers reference the renamed constants.
- Full suite 655 tests, 0 failures. `npm run build` succeeds (new bundle
  `main-BitvxJ9J.js` replaces `main-Dyvngl7R.js`; CSS unchanged).

## Stage 3 — cache/AI: stop per-open LLM calls (fingerprint leak)

### Reproduce (evidence-first)

Instrumented the property AI data assembly (`apps/analytics/views._property_ai_data`)
and computed the fingerprint via `apps.analytics.ai_service.data_fingerprint` with a
mocked `timezone.now` stepped across a day boundary. Across all 10 seeded properties
(day 20 → day 21, stable business data):

- **Before fix** — 7/10 properties flipped fingerprint. Flipping normalized paths,
  and only these, were:
  - `charts.spatialScatter[i].x`  (integer)
  - `charts.avgLifespanByChannel[i].avgLifespan`  (float, same numeric value)
- Both are `today − listing.start_date` for active listings (`start_date` set,
  `end_date` null or future) — pure clock drift, not a business change.
- Consultant path (`_consultant_ai_data`) stable across the same boundary.
- `engagementHeatScore` (both `kpis.*` and `marketIndicators.*`) did **not** flip in
  any tested property — disproved as a suspect.

### Root cause

`_VOLATILE_KEYS` in `ai_service.py` dropped only top-level keys
(`daysOnMarket`, `engagementHeatmap`, `exposureTimeline`, …). The two chart arrays
are nested inside `charts`, so `_normalize_for_fingerprint` (which only filters on
key name at each dict level) did not remove their clock-derived fields. A day
boundary therefore changed the fingerprint → cache miss → a fresh LLM call on open.

### Fix (minimal diff)

Added `spatialScatter` and `avgLifespanByChannel` to `_VOLATILE_KEYS`. Because
`_normalize_for_fingerprint` drops any dict key in that set at every nesting level,
the two clock-derived chart arrays are now excluded from the fingerprint — matching
the existing treatment of `engagementHeatmap` / `exposureTimeline`.

### Verification

- After fix: all 10 properties + 3 consultants stable across the day boundary
  (0 flipping paths).
- Acceptance tests T1–T5 added (`AIAcceptanceTests` in
  `apps/analytics/tests/test_ai_service.py`), provider mocked at `_chat_completion` /
  `_urlopen`. AI module: 31 tests pass. Full suite: 655 tests, 0 failures.

### Poll consolidation (measured)

Already implemented + tested (`apps/common/tests/test_phase5_caching.py`).
Measured with `CaptureQueriesContext`:

- notifications poll: 6 → 4 queries (2nd hit cached); mark-read → unreadCount fresh
  (4 → 3).
- ticket unread-count poll: 5 → 4 queries (2nd hit cached); mark-read invalidates.
- Fail-open verified via dead-cache mock.

No defect found — no poll code changed this stage.

### Cache audit conclusion

The AI description cache was the only proven defect (fingerprint leak above).
All other cache entries (AIInsightCache DB layer, list COUNT, reference/schema,
property report, dashboard, both polls, session cached_db) verified correct with
their invalidation triggers and fail-open paths intact. See the stage report for
the full table.
