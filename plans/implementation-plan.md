# Otclick-hh — план реализации персонального приложения откликов

Обновлено: 2026-09-07

## Текущий статус

Ветка разработки: `feature/persistent-vacancy-funnel`  
Draft PR: `#1`  
Реальная отправка HH: **выключена** (`ALLOW_REAL_APPLY=false`), новый sender не подключён к `worker_main`.

Целевая архитектура:

`HH web-session -> Search Sources -> persistent vacancy_pipeline -> enrichment -> hard filter -> LLM score -> mobile review -> cover draft -> exact-text approval -> persistent send queue -> HH submit/reconcile`

Главный принцип: **discovery/scoring и send физически разделены**. Сценарий старого проекта `нашёл -> ApplyJob -> сразу отправил` не является целевым runtime.

---

# 0. Safety и validation baseline

- [x] Feature-ветка и draft PR.
- [x] `ALLOW_REAL_APPLY=false` по умолчанию.
- [x] Общий kill-switch стоит в `form_filler.submit_response` до HH session/network submit.
- [x] `submit_prepared_form` проходит через тот же gate.
- [x] `worker_main` не запускает legacy apply runner (`want_apply=False`).
- [x] Persistent sender при disabled flag завершается **до чтения send queue и до HH access**.
- [x] Sender engine существует, но **не импортируется/не запускается `worker_main`**.
- [x] Exact-text approval привязан SHA-256 к сохранённому тексту.
- [x] Любое изменение текста после approval сбрасывает approval/hash.
- [x] После постановки активного send-job редактирование письма блокируется.
- [x] Safety tests покрывают disabled flag / snapshot mismatch / exact approved text / already responded / manual form.
- [ ] Полный выполняющийся CI baseline.

## GitHub Actions blocker

- [x] Workflow содержит `workflow_dispatch`, `push: main`, `push: feature/**`, `pull_request`.
- [x] В workflow добавлены backend pytest/ruff, frontend/ext typecheck/tests/build и static installer checks.
- [x] Проверен обычный feature push — `0 workflow_runs`.
- [x] Проверен `pull_request: reopened` закрытием/переоткрытием draft PR — `0 workflow_runs`.
- [x] Workflow присутствует и в `main`.
- [x] Через текущий GitHub connector доступны fetch/rerun существующих runs, но нет действия для первого `workflow_dispatch` и нет enable-Actions endpoint.
- [ ] Включить/проверить Actions в настройках репозитория либо выполнить первый run другим credential/tool.
- [ ] После появления run исправить все реальные pytest/tsc/build ошибки до green.

**Не считать ветку production-ready, пока этот пункт не закрыт.**

---

# 1. Данные кандидата

Источник истины:
- `01_Карьерное_позиционирование_CIO_CDTO(1).md`;
- `02_RFL_банк_достижений_и_фактов(1).md`.

- [x] `candidate_profile.json` с актуальным карьерным позиционированием.
- [x] `confirmed_facts.json`: ровно 22 подтверждённых facts/cases.
- [x] Claim guardrails отделены от достижений.
- [x] Пункты `нужно уточнить` не импортируются как факты.
- [x] `035_candidate_context.sql`: `candidate_profiles` + `candidate_facts`.
- [x] Deterministic loader без универсального Markdown parser.
- [x] Удалённые prepared facts при reload деактивируются.
- [x] Profile/facts/guardrails реально используются scorer.
- [x] Relevant facts/guardrails реально используются cover writer; использованные `fact_key` сохраняются.
- [x] Read-only API `/api/candidate-context`.
- [x] Mobile `/vacancies/profile`: roles, positioning, scale, industries, compensation, guardrails и все active facts.
- [ ] Редактирование candidate context из UI — только после первой калибровки, если будет полезно; сейчас source JSON остаётся контролируемым источником.

---

# 2. Persistent vacancy funnel

- [x] `034_vacancy_pipeline.sql`.
- [x] `vacancy_pipeline`, unique `(user_id, hh_vacancy_id)`.
- [x] `vacancy_pipeline_sources` many-to-many.
- [x] Lifecycle: discovered/scoring/scored/review/selected/letter_draft/approved/queued_to_send/sending/sent/rejected_by_user/hold/archived/score_error/send_error.
- [x] Persistence обновляет vacancy snapshot, не сбрасывая ручное решение пользователя.
- [x] Conditional lifecycle transitions защищают от concurrent update.
- [x] Новый discovery пишет напрямую в PostgreSQL, а не in-memory ApplyJob queue.
- [x] Full vacancy enrichment через authenticated HH web-session/cookies.
- [x] Enrichment автоматически выполняется перед scoring.
- [x] Removed/archived HH vacancy -> archived.
- [ ] Restart/recovery integration test с реальной PostgreSQL/Supabase stack.
- [ ] После live-калибровки удалить/заархивировать неиспользуемый legacy `vacancy_producer.py` / старую in-memory queue.

---

# 3. Источники вакансий

## 3.1 HH Search URL

- [x] Parser для hh.ru / regional `*.hh.ru` / hh.kz.
- [x] Query хранится ordered pairs, поэтому повторные `area`, `search_field`, `professional_role` сохраняются.
- [x] Неизвестные параметры не выбрасываются.
- [x] Backend preview + CRUD `/api/search-sources`.
- [x] URL change сбрасывает cursor.
- [x] Mobile `/vacancies/sources`: preview/save/enable-disable/delete/status/error.

## 3.2 Incremental discovery

- [x] Первый run: максимум 3 страницы свежей выдачи.
- [x] Принудительный `publication_time`.
- [x] Cursor хранит previous head vacancy IDs.
- [x] Следующий run идёт до overlap и останавливается.
- [x] Emergency cap 20 pages.
- [x] DB dedup между источниками.
- [x] Старые `applications` не возвращаются в новый backlog.
- [x] Cadence discovery = 5 минут.
- [ ] Source statistics: new / duplicate / hard-filtered / score-error cumulative counters.

## 3.3 Нативные HH автопоиски — web-session, без Bearer

Решение пользователя: реализовать, но live contract проверить после нескольких циклов разработки.

Подтверждено:
- [x] applicant cookies открывают `GET /applicant/autosearch.xml`;
- [x] это отдельный web-контур «Автопоиски вакансий»;
- [x] applicant Bearer/API не является зависимостью новой архитектуры;
- [x] `api.hh.ru/saved_searches/vacancies` не используется как основа MVP.

После live-проверки на аккаунте с заполненными автопоисками:
- [ ] read-only probe DOM/data-qa + inline JSON state;
- [ ] при client-side data снять фактический read-only internal XHR/fetch;
- [ ] подтвердить минимум `name + search criteria`, желательно stable id/new_count;
- [ ] импортировать как `hh_autosearch` Search Source;
- [ ] unchanged criteria сохраняют cursor, changed criteria сбрасывают cursor;
- [ ] исчезнувший HH autosearch не удаляет локальный source автоматически;
- [ ] runtime после импорта использует **наш** cursor, а не HH notification/new_count.

Manual Search URL остаётся гарантированным fallback.

## 3.4 Recommendations

- [ ] Опционально исследовать authenticated recommendations web flow после основного MVP.
- [ ] Нестабильный источник просто не включать.

**Не реализуем:** subscriptions/watches отдельных компаний.

---

# 4. Hard filter + LLM scoring

- [x] Conservative hard-filter только явных role mismatches.
- [x] Mixed/ambiguous strategic roles проходят в LLM.
- [x] Unknown industry/revenue/salary != reject.
- [x] Structured 0–100 score.
- [x] Components: role fit / scale / transformation mandate / industry-business context.
- [x] pros / risks / unknowns / confidence.
- [x] CIO transformation vs operations IT head.
- [x] CTO platform/product vs lead developer.
- [x] standalone business vs diversified holding logic.
- [x] LLM failure -> `score_error`, never fail-open.
- [x] Approved user rules can hard-reject or add scoring preference.
- [x] Pending/rejected rule proposals never enter scorer.

---

# 5. Mobile review

- [x] `/api/vacancies`: list/get/filter/pagination.
- [x] Source attribution.
- [x] Select / reject with reason / hold / return to review.
- [x] Terminal/send states protected from ordinary review clicks.
- [x] `/vacancies` mobile cards + responsive desktop view.
- [x] Company / title / salary / score / source / status.
- [x] Full vacancy text + score explanation + pros/risks/unknowns/confidence.
- [x] Lifecycle tabs.
- [x] Candidate context page in same section.
- [ ] Bulk select — после первого ручного прогона, чтобы сначала проверить card workflow.
- [ ] Отдельный desktop table mode — только если card mode неудобен.

---

# 6. Сопроводительные письма

- [x] Cover draft создаётся после user selection, не на discovery.
- [x] Письмо создаётся для каждой selected vacancy независимо от HH `letter_required`.
- [x] Контекст: selected resume + full vacancy + confirmed facts + guardrails + score anchors.
- [x] Numeric score не используется как аргумент в письме.
- [x] 500–750 символов, язык вакансии, 1–2 релевантных evidence.
- [x] Только подтверждённые facts; неизвестные `fact_key` блокируют draft.
- [x] Нет generic/template fallback, который мог бы стать sendable.
- [x] Mobile edit/regenerate.
- [x] Exact-text approval SHA-256.
- [x] Edit after approval -> `letter_draft` + approval cleared.

---

# 7. Persistent send queue

- [x] `037_send_queue.sql`.
- [x] Durable DB queue, не `asyncio.Queue`.
- [x] Queue принимает только approved exact-text snapshot.
- [x] Idempotency per pipeline vacancy; cancelled job можно requeue.
- [x] Mobile approve -> queue -> cancel.
- [x] Sender проверяет snapshot hash/resume/vacancy перед HH.
- [x] Pre-submit alive/archived/already-responded reconciliation.
- [x] `already_responded` -> sent without duplicate submit.
- [x] HH tests/forms -> manual_required, без auto-answer submit.
- [x] Uncertain submit -> HH reconciliation before failure.
- [x] Нет blind retry.
- [x] Нет employer-wide blacklist из одного ответа.
- [x] Engine не подключён к runtime.

Перед реальной активацией:
- [ ] DB lease / single-account lock: максимум один submit одновременно.
- [ ] Pause / Resume / Stop-after-current.
- [ ] Progress N/M/current/error/manual.
- [ ] Configurable safety interval.
- [ ] Retry/reset UI для failed/manual_required.
- [ ] Bulk queue/send after bulk select.
- [ ] Отдельное решение пользователя на включение sender в `worker_main`.

---

# 8. Накопление правил

- [x] `user_decision_reason` сохраняется.
- [x] LLM предлагает `rule` / `change` / `no_generalization`.
- [x] LLM не может активировать правило сам.
- [x] До approval показывается deterministic impact preview по backlog.
- [x] Approved rule получает version.
- [x] Active rule можно disable/enable.
- [x] Selective rescore — отдельное действие пользователя.
- [x] Старые selected/approved/queued/hold/rejected/sent решения автоматически не меняются.
- [x] Employer response/rejection не считается preference signal пользователя.
- [x] Mobile `/vacancies/rules` + `/vacancies/rules/manage`.

---

# 9. Context/version fingerprints

- [x] Candidate hash.
- [x] Active-rules hash.
- [x] Vacancy content hash.
- [x] Resume hash.
- [x] Score-anchor hash.
- [x] Scorer model + prompt version входят в score context fingerprint.
- [x] Writer model + prompt version входят в cover context fingerprint.
- [x] `score_stale` / `cover_stale` вычисляются advisory, не меняя lifecycle.
- [x] Missing context не ломает backlog — stale state становится unknown.
- [ ] После калибровки добавить удобные UI actions `пересчитать устаревшие` / `перегенерировать устаревшие` пачкой.

---

# 10. HH authentication: web-session first

- [x] Playwright login сохраняет encrypted HH cookies.
- [x] Если OAuth token exchange не сработал, cookies-only connection сохраняется как успешная.
- [x] `connected=true` определяется наличием valid web-session, а не Bearer.
- [x] Status API явно возвращает `has_api_token`.
- [x] `/api/hh/refresh` для cookies-only = `not_applicable`, без попытки OAuth refresh.
- [x] Account UI показывает `web-session / cookies`; refresh API token доступен только если token реально есть.
- [x] Discovery/enrichment/send используют web-session path.

---

# 11. Single-user install / update

- [x] Root `install.sh` для Ubuntu 24.04.
- [x] Установка Docker/Compose prerequisites.
- [x] Clone/update публичного `gest0r1/Otclick-hh`.
- [x] `infra/bootstrap.py`: secrets создаются только при отсутствии `.env`; update не использует `--force`.
- [x] Existing `.env` сохраняется.
- [x] `DISABLE_SIGNUP=true` по умолчанию и принудительно installer’ом.
- [x] Public signup не требуется: первый user создаётся service-role GoTrue admin endpoint.
- [x] Installer сохраняет только user UUID/email; сгенерированный пароль не записывается в `.env`.
- [x] После first user автоматически загружаются candidate profile + 22 facts.
- [x] Backend image включает prepared data/scripts.
- [x] Caddy — единая browser origin.
- [x] API/Kong/frontend host ports bind только к `127.0.0.1`; публичны только Caddy 80/443.
- [x] `OTCLICK_DOMAIN` -> Caddy automatic HTTPS.
- [x] Без domain можно временно работать по HTTP/IP; installer выдаёт warning.
- [x] Migrations выполняются отдельным one-shot compose service.
- [x] Health checks backend/frontend/Auth.
- [x] Update: PostgreSQL custom-format backup **до git fetch/pull**.
- [x] Update: timestamped `.env` backup.
- [x] Install/update log в `/var/log/otclick-hh`.
- [x] Rollback procedure `infra/ROLLBACK.md`.
- [x] Static regression tests фиксируют installer safety contract.
- [x] Login UI приведён к single-user модели: регистрации в интерфейсе нет.
- [ ] Live install test на чистой Ubuntu 24.04 VM.
- [ ] Live update test на уже заполненной БД.
- [ ] После merge проверить one-command installer именно из `main`.

Планируемая production-команда после merge:

```bash
curl -fsSL https://raw.githubusercontent.com/gest0r1/Otclick-hh/main/install.sh -o /tmp/otclick-install.sh && sudo bash /tmp/otclick-install.sh
```

С доменом:

```bash
OTCLICK_DOMAIN=jobs.example.com sudo -E bash /tmp/otclick-install.sh
```

---

# 12. Калибровка 20–30 вакансий

- [ ] Проверить реальные Search URL sources.
- [ ] Снять filled `/applicant/autosearch.xml` contract и закончить autosearch import.
- [ ] Hard-filter precision.
- [ ] Score vs фактические решения пользователя.
- [ ] Rejection reason -> rule proposals.
- [ ] 20–30 cover drafts.
- [ ] Mobile UX.
- [ ] Restart/recovery/idempotency.
- [ ] Только после отдельного решения — ограниченный real-send test.

---

# 13. Auto mode — отдельным решением после калибровки

Не входит в обязательный MVP.

- [ ] minimum score;
- [ ] allowed hard conditions;
- [ ] daily limit;
- [ ] max consecutive errors;
- [ ] kill switch;
- [ ] forms/unknown required fields manual;
- [ ] explicit enable/disable UI.

---

## Ближайший порядок работ

1. Получить выполняющийся CI или эквивалентный полный local build/test baseline.
2. Проверить installer на чистой Ubuntu 24.04 и повторный update с backup/migrations.
3. Подготовить первую ручную калибровку мобильного workflow без real sends.
4. Когда пользователь сможет проверить HH аккаунт — снять filled contract `/applicant/autosearch.xml` и закончить web-session autosearch import.
5. После результатов 20–30 вакансий доработать bulk UX, правила и score/cover prompts.
6. Sender activation и auto mode — только по отдельному решению пользователя.
