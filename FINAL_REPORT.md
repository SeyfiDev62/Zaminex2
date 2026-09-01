# گزارش نهایی — زامینه‌کس (Zaminex2)

**مرحله ۱۶ (پایانی) — پویش کامل رگرسیون + گزارش نهایی مالک**

وضعیت نهایی: **تمام ۱۴ باگ رفع شد، سوئیت کامل ۶۹۷/۰ و vitest ۲۴/۲۴ سبز.**

---

## الف) نمای کلی

- **تعداد مراحل:** ۱۶ مرحله (مرحله‌ی ۱ راه‌اندازی پایه + مراحل ۲ تا ۱۵ رفع ۱۴ باگ + مرحله‌ی ۱۶ گزارش نهایی و پویش رگرسیون).
- **پیشروی تعداد تست:** `650 → 697` (+۴۷) با تفکیک مرحله‌به‌مرحله در جدول باگ‌ها.
- **vitest:** ۲۴/۲۴ (سوئیت `iranLocations.test.ts` از مرحله‌ی ۷).
- **باندل:** `main-CZ8m8iVI.js` (همراه CSS `main-DGkrzoh4.css`) — ساخت مجدد قطعی (deterministic) و بایت‌به‌بایت منطبق با باندل کامیت‌شده.

### فهرست کامیت‌ها (از قدیم به جدید)

| مرحله | باگ | هش | موضوع |
|---|---|---|---|
| ۲ | ۱ | `4f2625c` | fix(properties): return to property detail after edit save |
| ۳ | ۲ | `8f55731` | fix(cache): stop per-open AI model calls from clock-derived chart fields |
| ۴ | ۳ | `a1b5f94` | fix(map): default map view to Mazandaran |
| ۵ | ۴ | `3ba6a00` | fix(dashboard): bound and truncate the consultant legend |
| ۶ | ۵ | `d9eb4bf` | fix(map-picker): overlay chips, teardrop centre pin, helper line |
| ۷ | ۶ | `840b249` | fix(map-picker): precise geocoding with NO-MOVE fallback |
| ۸ | ۷ | `a896155` | fix(attributes): refuse bound delete, un-stale ?all=1 lists |
| ۹ | ۸ | `b5b691d` | fix(reports): harden PDF font loading, add AI + tasks/log sections |
| ۱۰ | ۹ | `0c22665` | fix(reports): one canonical access rule for JSON/CSV/PDF |
| ۱۱ | ۱۰ | `2acab47` | fix(properties): remove deleted property row in the same tick |
| ۱۲ | ۱۱ | `a0699da` | feat(attributes): essential/non-essential category + migration |
| ۱۳ | ۱۲ | `bc034dc` | fix(properties): serve property images to all consultants, lock tabs |
| ۱۴ | ۱۳ | `caf2e23` | fix(activity): render activity-log status changes in Persian |
| ۱۵ | ۱۴ | `4f37448` | fix(tickets): fully Persian overdue-SLA label |

---

## ب) جدول ۱۴ باگ

| # | باگ (گزارش مالک) | علت ریشه‌ای | رفع | تست‌ها | کامیت |
|---|---|---|---|---|---|
| ۱ | پس از ذخیره‌ی ویرایش ملک، به صفحه‌ی جزئیات برنمی‌گشت | جریان ثبت، هیچ ناوبری انجام نمی‌داد | `EditPropertyWizard` بعد از ذخیره‌ی موفق `navigate("property-detail")`؛ `App.submitProperty` مقدار `editingPropertyId` را پاک می‌کند | ۰ جدید (بررسی زمان‌اجرا) | `4f2625c` |
| ۲ | هر بار باز کردن صفحه، دوباره به مدل هوش‌مصنوعی درخواست می‌رفت | نشتی اثرانگشت از فیلدهای نموداری وابسته‌به‌ساعتِ لانه‌شده | افزودن `spatialScatter` و `avgLifespanByChannel` به `_VOLATILE_KEYS` | ۵ (`AIAcceptanceTests`) | `8f55731` |
| ۳ | نقشه به‌جای مازندران روی تهران/سطح کشور باز می‌شد | ثابت‌های مرکز/زوم پیش‌فرض روی تهران | `DEFAULT_VIEW_CENTER=[36.4,53.2]` و `DEFAULT_VIEW_ZOOM=8` | ۰ جدید | `a1b5f94` |
| ۴ | راهنمای مشاوران زیر نقشه‌ی ادمین با تعداد زیاد بی‌نظم و بی‌نهایت می‌شد | چیدمان `flex-wrap` بدون محدودیت | شبکه‌ی اسکرول‌دار محدود (`grid` + `max-h-36 overflow-y-auto`) | ۰ جدید | `3ba6a00` |
| ۵ | برچسب‌های مختصات و نشانگر مرکز روی نقشه دیده نمی‌شدند | چیپ‌ها z-index نداشتند | `z-[1000]` برای هر دو چیپ؛ نشانگر سنجاقی سبز؛ خط راهنما تک‌خطی | ۰ جدید | `d9eb4bf` |
| ۶ | زوم دقیق استان/شهر/محله درست کار نمی‌کرد | نام‌های محله‌ی نازک/عمومی + پنجره‌ی محدود تنگ | نردبان واریانت‌ها + عریض‌کردن viewbox + fallback بدون جابه‌جایی | ۲۴ (vitest) | `840b249` |
| ۷ | حذف ویژگیِ متصل، اتصال یتیم می‌گذاشت و لیست کهنه می‌ماند | `perform_destroy` فقط ویژگی‌های ثابت را رد می‌کرد؛ `get_queryset` کوئری‌ست کش‌شده برمی‌گرداند | گارد اتصال فعال + `queryset.all()` | ۶ (`AttributeDeleteTests` و…) | `a896155` |
| ۸ | PDF خالی/خطای ۵۰۰ انگلیسی وقتی فونت خراب باشد | `_register_font` بدون اعتبارسنجی و مدیریت خطا | اعتبارسنجی فونت + `_FontUnavailable` + `.gitattributes` + بخش AI و وظایف و لاگ | ۸ (۴ کلاس PDF) | `b5b691d` |
| ۹ | مشاور از ملک‌های اشتراکی گزارش نمی‌گرفت (JSON/CSV رد، PDF قبول) | `get_property_for_user_or_403` قانون دسترسی را خودش بازنویسی کرده بود | تفویض به `can_access_property` (یک قانون واحد برای سه فرمت) | ۲ (`PropertyReportAccessMatrixTests`) | `0c22665` |
| ۱۰ | بعد از حذف ملک، ردیف تا رفرش صفحه می‌ماند | state محلی صفحه هرگز به‌روز نمی‌شد | `applyLocalRemoval` + `await onDelete` + برگشت صفحه | ۰ جدید (فقط فرانت‌اند) | `2acab47` |
| ۱۱ | دسته‌بندی ویژگی‌ها (ضروری/غیرضروری) وجود نداشت | فیلد `category` نبود | مایگریشن + `classify_attribute` + API + تب سوم | ۱۰ (`ClassifyAttributeTests` و…) | `a0699da` |
| ۱۲ | تصاویر ملکِ سایر مشاوران رندر نمی‌شد؛ دکمه‌ی «جزئیات» مشاور دیده می‌شد | media 403 برای غیرمالک؛ `showConsultantDetails` نادرست | تصویر ملک ← هر کاربر احرازشده؛ گیت `isOwn` + قفل تب‌ها | ۵ (`ProtectedMediaTests`) | `bc034dc` |
| ۱۳ | لاگ تغییر وضعیت‌ها نیمه‌انگلیسی بود | کدهای خام وضعیت در توضیحات لاگ | `labels.py` + ترجمه‌ی زمان-نمایش ردیف‌های قدیمی | ۱۱ (`NewRowPersianStatusTests` و…) | `caf2e23` |
| ۱۴ | برچسب «SLA گذشته» نیمه‌انگلیسی بود | رشته‌ی سخت‌کدشده | `SLA_OVERDUE_LABEL = "مهلت پاسخ گذشته"` در هر سه محل | ۰ جدید (ثابت رشته) | `4f37448` |

---

## ج) فهرست تغییرات (Change Inventory)

### فایل‌های بک‌اند (`ZaminexB/apps/…`)

| فایل | خلاصه |
|---|---|
| `activity/labels.py` (جدید) | منبع واحد برچسب فارسی وضعیت/نوع برای لاگ فعالیت |
| `activity/signals.py` | توضیحات تغییر وضعیت ملک/آگهی به فارسی |
| `activity/views.py` | ترجمه‌ی توضیحات ذخیره‌شده در endpoint فهرست |
| `activity/tests/test_status_persian.py` (جدید) | تست‌های ردیف جدید/قدیمی/واحد |
| `analytics/ai_service.py` | افزودن کلیدهای فرّار به اثرانگشت کش |
| `analytics/tests/test_ai_service.py` | تست‌های پذیرش AI (پایدار بودن اثرانگشت) |
| `basics/categorization.py` (جدید) | `classify_attribute` (قانون ضروری/غیرضروری) |
| `basics/models.py` | فیلد `category` روی Attribute |
| `basics/serializers.py` | افشای فیلد `category` در API |
| `basics/views.py` | رفع کهنگی لیست (`queryset.all()`) |
| `basics/migrations/0003_attribute_category.py` (جدید) | مایگریشن افزودن و پرکردن `category` |
| `basics/tests/test_attribute_admin.py` | تست‌های حذف متصل/لیست آنی |
| `basics/tests/test_attribute_category.py` (جدید) | تست‌های دسته‌بندی |
| `basics/tests/test_api.py` | به‌روزرسانی برای معنای حذف متصل |
| `common/media.py` | دسترسی تصویر ملک ← هر کاربر احرازشده |
| `common/tests/test_security.py` | `ProtectedMediaTests` (ماتریس دسترسی رسانه) |
| `common/tests/test_detail_route_visibility.py` | استفاده از ویژگی غیرمتصل در تست حذف |
| `reports/pdf.py` | سخت‌سازی فونت + بخش AI/وظایف + ترجمه‌ی لاگ |
| `reports/services.py` | قانون دسترسی واحد (`can_access_property`) |
| `reports/tests.py` | کلاس‌های PDF + ماتریس دسترسی |

### فایل‌های فرانت‌اند (`ZaminexF/src/…`)

| فایل | خلاصه | نکته‌ی سازگاری استایل |
|---|---|---|
| `app/App.tsx` | `deleteProperty` نتیجه برمی‌گرداند؛ `submitProperty` ناوبری | موجود |
| `features/attributes/pages/AttributesPage.tsx` | تب «دسته‌بندی ویژگی‌ها» | دو تب قبلی بایت‌به‌بایت بدون تغییر |
| `features/consultants/components/PropertyLocationsMap.tsx` | نمای پیش‌فرض مازندران | فقط مقدار ثابت |
| `features/dashboard/AdminDashboard.tsx` | شبکه‌ی اسکرول‌دار راهنما | منطق `useMemo` بدون تغییر |
| `features/properties/PropertiesPage.tsx` | حذف محلی ردیف + `cache:"no-store"` | موجود |
| `features/properties/components/EditPropertyWizard.tsx` | ناوبری بعد از ذخیره | موجود |
| `features/properties/pages/PropertyDetail.tsx` | گیت `isOwn` + قفل تب‌ها + گیت دکمه‌ی گزارش | الگوی `EmptyState` موجود |
| `features/tickets/pages/TicketsPage.tsx` | `SLA_OVERDUE_LABEL` | استایل قرمز/خطر بایت‌به‌بایت |
| `shared/components/ui/PropertyDistributionMap.tsx` | نمای پیش‌فرض مازندران | فقط مقدار ثابت |
| `shared/components/ui/PropertyMapPicker.tsx` | چیپ‌ها/سنجاق/زوم دقیق | منطق تعامل بدون تغییر |
| `shared/lib/iranLocations.ts` | ژئوکدینگ + نمای پیش‌فرض | — |
| `shared/lib/iranLocations.test.ts` (جدید) | ۲۴ تست vitest | — |
| `shared/lib/types.ts` | نوع `onDelete` → `Promise<boolean>` | تغییر نوع پشتیبان |

### مایگریشن‌ها

- `0003_attribute_category.py` — تنها مایگریشن جدید بچ (بقیه‌ی مراحل بدون تغییر schema بودند).

### باندل و زیرساخت

- **باندل:** `main-CZ8m8iVI.js` (گیت-بلاپ `78f432b…`) + CSS `main-DGkrzoh4.css`.
- **ریشه:** `.gitattributes` (جدید) — علامت‌گذاری فونت‌ها/تصاویر/PDF به‌عنوان `binary` برای جلوگیری از خرابی checkout.
- **وابستگی dev:** `vitest@^3.2.7` در `package.json`/`package-lock.json`.

---

## د) چک‌لیست دستی یکپارچه‌ی مالک (۱۴ مورد)

1. **باگ ۱ — بازگشت بعد از ویرایش:** یک ملک را ویرایش و ذخیره کنید → باید مستقیماً به صفحه‌ی «جزئیات ملک» برگردید (نه ماندن روی فرم).
2. **باگ ۲ — کش AI:** صفحه‌ی گزارش/داشبورد را باز کنید؛ بستن و بازکردن مجدد نباید درخواست جدید به مدل هوش‌مصنوعی بفرستد (فقط اولین بازدید).
3. **باگ ۳ — نمای نقشه:** در «افزودن/ویرایش ملک»، داشبورد ادمین و نقشه‌ی مشاور، نقشه باید روی استان مازندران در زوم ۸ باز شود.
4. **باگ ۴ — راهنمای مشاوران:** داشبورد ادمین با تعداد زیاد مشاور → راهنما باید در قاب اسکرول‌دار و مرتب بماند (نام‌های بلند کوتاه‌شده با «…»).
5. **باگ ۵ — برچسب‌های نقشه:** در نقشه، برچسب مختصات (پایین-چپ) و نشانگر سبز مرکز باید به‌وضوح دیده شوند.
6. **باگ ۶ — زوم دقیق:** جستجوی شهر/محله‌ی مازندران → دوربین دقیق روی محل؛ در شکست، نقشه نباید جابه‌جا شود و پیام کوتاه «یافت نشد» نمایش یابد.
7. **باگ ۷ — مدیریت ویژگی:** حذف ویژگیِ متصل به نوع/معامله → خطای «ابتدا اتصالات را حذف کنید»؛ افزودن ویژگی جدید → بلافاصله در لیست (بدون رفرش).
8. **باگ ۸ — PDF:** خروجی «گزارش کامل» → PDF چندصفحه‌ای کامل با بخش‌های AI، وظایف و لاگ‌ها.
9. **باگ ۹ — گزارش اشتراکی:** مشاور روی ملک اشتراکی → هر سه فرمت JSON / CSV / PDF باز شود.
10. **باگ ۱۰ — حذف ملک:** حذف یک ملک در فهرست → ردیف در همان لحظه حذف شود (بدون F5)؛ حذف ناموفق ردیف را دست‌نخورده نگه دارد.
11. **باگ ۱۱ — دسته‌بندی ویژگی:** تب «دسته‌بندی ویژگی‌ها» → دو گروه «ضروری (n)» و «غیرضروری (n)» با عمل جابه‌جایی.
12. **باگ ۱۲ — ملک مشاور دیگر:** مشاور B ملک غیراشتراکی مشاور A را باز کند → تصاویر و گالری بارگذاری شوند، دکمه‌ی «جزئیات» مشاور نباشد، سه تب (آگهی‌ها/وظایف/پیگیری‌ها) با آیکون قفل و جمله‌ی «شما به … این ملک دسترسی ندارید».
13. **باگ ۱۳ — لاگ فارسی:** تغییر وضعیت یک ملک → ردیف جدید لاگ کاملاً فارسی؛ باز کردن «گزارش فعالیت‌ها» → ردیف‌های قدیمی انگلیسی نیز فارسی نمایش داده شوند؛ خروجی PDF → ردیف‌های لاگ قدیمی فارسی در جدول.
14. **باگ ۱۴ — مهلت پاسخ:** تیکت سررسیدگذشته → «مهلت پاسخ گذشته» قرمز در ردیف فهرست و در بَج جزئیات؛ گزینه‌ی فیلتر «مهلت پاسخ» با همین برچسب؛ تیکت غیرسررسید بدون برچسب.

---

## هـ) فهرست موارد باز (Open Items) + مراحل بازتولید و برآورد تلاش

1. **مرحله‌ی ۷ — بررسی اینترنت واقعی OSM (سمت کاربر):** نردبان واریانت‌ها فقط با payload های MOCK تأیید شده (Nominatim از سندباکس در دسترس نیست). **بازتولید:** روی ماشین با اینترنت واقعی، یک محله‌ی واقعی مازندران (که در OSM وجود دارد) در انتخابگر نقشه جستجو کنید و ببینید دوربین روی آن می‌نشیند. **تلاش:** ~۱۵ دقیقه، یک‌بار.

2. **مرحله‌ی ۱۳ — پنهان‌کردن بخش کارشناسی برای غیرمالک:** دانلود گزارش کارشناسی روی ملک غیرمالک 403 است (عمدی، حساس). **بازتولید:** مشاور B ملک غیراشتراکی A را باز کند → تب «گزارش کارشناسی» → توست 403. **پیشنهاد:** به‌جای توست، بخش کارشناسی برای غیرمالک کلاً پنهان شود. **تلاش:** کم (یک گیت فرانت‌اند).

3. **مرحله‌ی ۱۴ — برچسب‌های انگلیسی choice مدل‌ها (سراسری UI):** برچسب‌های `*_display` مدل‌ها هنوز انگلیسی است (`Available`, `Pending`, …). فید فعالیت از طریق `labels.py` فارسی شده، اما برچسب‌های خام یک قرارداد UI پیشین است. **بازتولید:** هر فرم/admin که از `get_*_display` استفاده می‌کند، برچسب انگلیسی نشان می‌دهد. **پیشنهاد:** ارتقای خود برچسب‌ها به فارسی (تغییر سراسری فرم‌ها/سریالایزر/admin). **تلاش:** متوسط.

(هیچ مورد جدیدی از پویش مرحله‌ی ۱۶ اضافه نشد — هیچ رگرسیونی یافت نشد.)

---

## و) محیط و بازتولیدپذیری

دستورالعمل بازسازی محیط پس از هر پاک‌شدن سندباکس (برای مالک یا هر نشست آینده):

1. **وضعیت Git:** `git fetch origin arena/01a05300-zaminex2` سپس `git reset --hard FETCH_HEAD` (تاریخچه خطی است؛ ریموت همه‌ی کامیت‌های مرحله را دارد).
2. **venv بک‌اند:** `python3 -m venv .venv` + `.venv/bin/pip install -r ZaminexB/requirements.txt`.
3. **PostgreSQL:** دانلود `pgserver==0.1.4` (wheel)، جابه‌جایی `pginstall` به `/home/user/pg/pginstall`، ساخت symlink ناقص libpq (`ln -s libpq.so.5.16 libpq-084d956f.so.5.16`)، کامپایل `pg_trgm` از سورس `REL_16_2` (`make USE_PGXS=1 … install`)، `initdb`، `pg_ctl start`، `CREATE DATABASE zaminex`، بازیابی `zaminex_backup.sql` (پس از حذف خطوط `\restrict`/`\unrestrict`)، `manage.py migrate`.
4. **فرانت‌اند:** `npm install` در `ZaminexF` (نصب `vitest`).
5. **گیت پایه:** `export DATABASE_URL=postgres://zaminex:zaminex@127.0.0.1:5432/zaminex` و `export LD_LIBRARY_PATH=/home/user/pg/pginstall/lib`؛ سپس `manage.py test` باید **۶۹۷/۰** باشد.

---

## ز) گیت نهایی

**بچ کامل شد — تمام ۱۴ مرحله‌ی رفع باگ (مراحل ۲ تا ۱۵) سبز؛ ۶۹۷/۰ + ۲۴/۲۴؛ چک‌لیست دستی مالک پیوست؛ فهرست موارد باز پیوست.**

در انتظار پاس دستی مالک و تصمیم‌گیری درباره‌ی موارد باز.
