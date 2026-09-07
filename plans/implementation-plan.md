# Otclick-hh — план реализации персонального приложения откликов

Обновлено: 2026-09-07

## Статус / выполненные коммиты

- [x] Исходная архитектура `Otclick-hh` и `hh-auto` разобрана.
- [x] Исследованы дополнительные HH-проекты и варианты источников.
- [x] Основная модель источника: persistent `Search Source` + собственный cursor.
- [x] Нативный HH автопоиск рассматривается как способ импорта/настройки source, но runtime от его недокументированного API не зависит.
- [x] Подписки на отдельные компании исключены из продукта.
- [x] Ветка разработки: `feature/persistent-vacancy-funnel`.
- [x] `0b5ad962` — persistent Search Sources + vacancy funnel schema + URL parser.
- [x] `fe1df614` — `ALLOW_REAL_APPLY=false` + общий backend kill-switch.
- [x] `96efa7b9` — Search Source API + incremental discovery + разрыв discovery/legacy apply runner.
- [x] `b9b380a6` — curated candidate profile + 22 confirmed facts + claim guardrails + loader.
- [x] `c11bf100..9c3bda67` — backend `Vacancies` backlog/review API + full HH vacancy web-session enrichment + review/parser tests.
- [x] `24d6a92b..da2f40b8` — structured hard filter/scoring + worker orchestration + fail-closed tests.
- [x] `b9d416d2..e9ca831a` — mobile `Vacancies`, Search Sources UI, cover-letter draft/edit/approval/queue controls.
- [x] `8eaa6e54..30a98abb` — exact-text approval + persistent `application_send_queue` + queue safety tests.
- [x] `88af7619..12974177` — persistent sender engine + reconciliation/safety tests; **engine не подключён к `worker_main`**.
- [ ] CI baseline: workflow расширен на `feature/**` и frontend build (`550ca803`), но GitHub по-прежнему создаёт `0 workflow_runs`; production-ready состояние не заявлять до восстановления CI/локального полного прогона.

> После каждого законченного блока отмечать `[x]` и добавлять commit SHA. Не считать задачу завершённой только потому, что схема или helper уже существуют: acceptance отмечается после подключения runtime/UI и тестов.

---

## Целевая архитектура

`HH Search Sources -> PostgreSQL vacancy_pipeline -> hard filter -> LLM score -> mobile review -> selected -> personalized draft -> exact-text approval -> persistent send queue -> sequential HH submit -> verification -> applications log`

Принцип: **discovery/scoring физически отделены от send**. Старый сценарий `нашёл -> ApplyJob -> apply_one` не является целевым путём.

---

# Этап 0. Safety и baseline

- [x] Рабочая feature-ветка.
- [x] `ALLOW_REAL_APPLY=false` по умолчанию в config и `.env.example`.
- [x] Kill-switch стоит в общем `form_filler.submit_response` до HH session/network POST.
- [x] `submit_prepared_form` проходит через тот же kill-switch.
- [x] Unit tests подтверждают: при disabled gate HH session не открывается.
- [x] `worker_main` больше не запускает legacy auto-apply runner из `worker_enabled`; runtime передаёт ему `want_apply=False`.
- [x] Persistent sender при `ALLOW_REAL_APPLY=false` возвращается **до чтения send queue, HH cookies или любых HH-запросов**.
- [x] Очередь можно наполнить/отменить, но sender engine не импортируется и не запускается `worker_main`.
- [x] Текст отправки связан SHA-256 hash с точным одобренным draft; изменение текста сбрасывает approval.
- [ ] Полный backend/frontend/build CI baseline.

**Acceptance:** discovery/review/scoring/draft/approval/queue можно запускать без риска случайного реального отклика.

---

# Этап 1. Данные кандидата

Источник истины:
- `01_Карьерное_позиционирование_CIO_CDTO(1).md`;
- `02_RFL_банк_достижений_и_фактов(1).md`.

- [x] Подготовлен `backend/data/candidate/candidate_profile.json`.
- [x] Приоритет ролей зафиксирован из актуального source: CDTO -> CIO+CDTO -> CIO -> CTO с ограничением по CTO.
- [x] Зафиксированы актуальные target scale/industries/org level/compensation из source.
- [x] Подготовлены 22 подтверждённых facts/cases в `confirmed_facts.json`.
- [x] Пункты `нужно уточнить` не импортированы как достижения.
- [x] Claim guardrails сохранены отдельно.
- [x] Миграция `035_candidate_context.sql`: `candidate_profiles` + `candidate_facts`.
- [x] Deterministic loader `backend/scripts/load_candidate_data.py`.
- [x] Удалённые из prepared data факты при повторной загрузке деактивируются.
- [x] Profile/facts/guardrails подключены в structured scorer.
- [x] Relevant confirmed facts/guardrails подключены в cover writer; использованные `fact_key` сохраняются в `cover_letter_meta`.
- [ ] API/UI просмотра и ручной корректировки реально используемых profile/facts.

**Решение:** универсального Markdown parser в runtime нет и не будет.

---

# Этап 2. Постоянная воронка вакансий

- [x] `034_vacancy_pipeline.sql`.
- [x] `vacancy_pipeline`, unique `(user_id, hh_vacancy_id)`.
- [x] Lifecycle: discovered/scoring/scored/review/selected/letter_draft/approved/queued_to_send/sending/sent/rejected/hold/error.
- [x] `vacancy_search_sources`.
- [x] many-to-many `vacancy_pipeline_sources`.
- [x] Persistence helper обновляет snapshot, но не сбрасывает user decision.
- [x] Conditional/atomic lifecycle transition helper.
- [x] Новый runtime discovery пишет напрямую в `vacancy_pipeline`, не создавая `ApplyJob`.
- [x] `worker_main` использует discovery path; legacy apply runner не является потребителем найденных вакансий.
- [x] Read-only full vacancy enrichment через web-session/cookies сохраняет `description` в pipeline.
- [x] Backend API чтения backlog и одной вакансии с источниками.
- [x] Backend review actions: выбрать / отклонить с причиной / отложить / вернуть в review.
- [x] Enrichment автоматически выполняется непосредственно перед hard filter/scoring.
- [ ] Удалить/заархивировать старый `vacancy_producer.py` и in-memory queue после переноса оставшихся зависимостей.
- [ ] Restart/recovery integration tests с реальной БД.

**Acceptance:** источник истины по найденным вакансиям — PostgreSQL; перезапуск не теряет backlog.

---

# Этап 3. Источники вакансий

## 3.1 Search Source / HH search URL

Каждое направление хранит: name, origin/source type, query params, optional resume binding, cursor, enabled, last check/success/error.

- [x] URL parser принимает `hh.ru`, regional `*.hh.ru`, `hh.kz`.
- [x] Query params хранятся ordered pairs, поэтому повторяющиеся `area`, `search_field`, `professional_role` не теряются.
- [x] Неизвестные параметры сохраняются и показываются как unsupported.
- [x] Backend endpoint preview URL перед сохранением.
- [x] Backend CRUD `/api/search-sources`.
- [x] Изменение URL сбрасывает старый cursor.
- [x] Mobile UI `/vacancies/sources`: вставить URL -> preview -> сохранить -> enable/disable/delete + last run/error.

## 3.2 Incremental ingestion

- [x] Первый запуск ограничен 3 страницами свежей выдачи, а не полным историческим SERP.
- [x] Runtime принудительно сортирует по `publication_time`.
- [x] Cursor хранит head vacancy ids предыдущего успешного цикла.
- [x] Последующие циклы идут до overlap с предыдущим head и останавливаются раньше полного обхода.
- [x] Максимальный аварийный scan cap — 20 страниц; отсутствие overlap фиксируется в `last_error`.
- [x] DB dedup защищает от повторов и пересечений источников.
- [x] Уже существующие `applications` не добавляются заново в новый backlog.
- [x] Discovery cadence — 5 минут; 15-секундный reconcile не вызывает новый HH search каждый раз.
- [ ] Расширить source statistics: new / duplicate / hard-filtered / score-error, а не только текущий run summary.

## 3.3 Нативные автопоиски HH через web-session

Предпочтительный UX импорта, но не runtime dependency и **без Bearer token**.

Подтверждено на свежем live-contract HH июля 2026:
- авторизованный applicant web-session открывает `GET /applicant/autosearch.xml`;
- это отдельный web-контур «Автопоиски вакансий»;
- на проверенном аккаунте список был пуст, поэтому контракт заполненной карточки ещё нужно снять на живом аккаунте пользователя.

- [x] Подтверждён web-route `/applicant/autosearch.xml` через applicant cookies.
- [x] Решение: не строить импорт на `api.hh.ru/saved_searches/vacancies` и не требовать applicant Bearer token.
- [ ] Read-only probe заполненной страницы: DOM/data-qa + inline JSON state + наблюдаемые internal XHR/fetch без POST/PUT/DELETE.
- [ ] На живом аккаунте подтвердить минимум: `name + search URL/query`, желательно стабильный HH autosearch id и `new_count`.
- [ ] Если данные есть в inline state — использовать его как primary parser; DOM/search links как fallback.
- [ ] Если страница client-side — снять фактический внутренний read-only XHR и использовать его через ту же web-session.
- [ ] Импортировать criteria как `source_type=hh_autosearch`; хранить origin/external id/metadata отдельно от runtime cursor.
- [ ] Повторный импорт: изменённые criteria сбрасывают cursor; неизменённые сохраняют его.
- [ ] Sync не удаляет локальный source автоматически, если автопоиск исчез в HH — помечает origin missing и оставляет решение пользователю.
- [ ] После импорта выполнять ingestion собственным Search Source + cursor; HH `new_count` только справочный.
- [x] Manual Search URL остаётся гарантированным fallback и не блокирует MVP.

## 3.4 HH recommendations

Только дополнительный канал, не основной поиск.

- [ ] Проверить authenticated endpoint/flow.
- [ ] Если стабилен — небольшой capped source.
- [ ] Все рекомендации проходят тот же hard filter + scoring.
- [ ] Нестабильный endpoint просто отключается; MVP от него не зависит.

**Не реализуем:** подписки на отдельные компании.

---

# Этап 4. Hard filter + LLM scoring

- [x] Консервативный hard filter для явных role mismatches; неоднозначные роли идут в LLM.
- [x] Каждое hard rejection хранит конкретную reason.
- [x] Unknown industry/revenue/salary не является automatic reject.
- [x] Scorer получает full vacancy + structured candidate profile/facts.
- [x] Score 0–100.
- [x] Компоненты: role fit / scale / transformation mandate / industry-business context.
- [x] Сохраняются pros / risks / unknowns / confidence.
- [x] Prompt различает CIO transformation vs operations IT head; CTO/platform vs lead developer; standalone business vs holding function.
- [x] LLM failure -> `score_error`, не fail-open.
- [x] Низкий score остаётся видимым в backlog/UI.
- [ ] Пользовательские stop words / explicit excluded companies / approved learned hard rules — этап накопления правил.

---

# Этап 5. Vacancies / mobile review

- [x] Backend `/api/vacancies`: list/get + lifecycle filter/pagination.
- [x] Backend source attribution в выдаче backlog.
- [x] Backend действия: Выбрать / Отклонить / Отложить / Вернуть в review.
- [x] Отклонение требует `user_decision_reason`; terminal/send states обычным review action не двигаются.
- [x] Read-only `/api/vacancies/{id}/enrich` догружает full description через HH web-session.
- [x] Отдельная страница `Vacancies`, не `Applications`.
- [x] Mobile cards; desktop использует тот же responsive card view.
- [x] Company / role / salary / score / sources / status.
- [x] Full description + score explanation + pros/risks/unknowns/confidence.
- [x] UI: Выбрать / Отклонить / Отложить / Вернуть.
- [x] UI rejection reason.
- [x] Lifecycle tabs.
- [ ] Bulk select.
- [ ] При необходимости добавить отдельный desktop table mode после мобильной калибровки.

---

# Этап 6. Сопроводительные письма

- [x] Адаптированы принципы `hh-auto`, без его Playwright/delay runtime.
- [x] Письмо генерируется для каждой selected vacancy, даже если HH формально не требует letter.
- [x] Контекст: selected HH resume + relevant confirmed facts + guardrails + full vacancy + scoring anchors.
- [x] Writer не получает numeric score как аргумент убеждения.
- [x] 500–750 символов, язык вакансии, ближайший релевантный кейс, 1–2 evidence, employer/role-specific close.
- [x] Только подтверждённые факты; `fact_key` сохраняются в metadata.
- [x] Persist draft + mobile edit/regenerate.
- [x] Approval связывается SHA-256 с точным сохранённым текстом.
- [x] Edit after approval -> обратно `letter_draft`, approval/hash очищаются.
- [x] После постановки активного send-job редактирование блокируется.
- [x] LLM failure не создаёт auto-sendable fallback.

---

# Этап 7. Persistent send queue

- [x] `037_send_queue.sql`: отдельная `application_send_queue`, не `asyncio.Queue`.
- [x] Только approved vacancy с совпадающим exact-text hash может быть queued.
- [x] В queue хранится snapshot точного одобренного текста и resume/vacancy identity.
- [x] Queue per vacancy idempotent; cancelled job можно безопасно requeue.
- [x] UI: `одобрить текст -> поставить в очередь -> отменить очередь`.
- [x] Pre-submit engine проверяет approval snapshot, vacancy alive, archived, already responded.
- [x] Already responded на HH -> reconciliation `sent`, повторный submit не выполняется.
- [x] Network uncertainty/rejected submit -> сначала повторная сверка HH, затем fail; автоматического blind retry нет.
- [x] Forms/tests -> `manual_required`; persistent sender их не заполняет и не отправляет автоматически.
- [x] Новый sender не использует employer-wide auto-blacklist из legacy `apply_one`.
- [x] Safety tests: disabled flag -> ни queue read, ни HH access; mismatch -> no submit; exact text -> exact submit argument.
- [x] Sender engine **не подключён** к `worker_main`; реальный runtime submit не активирован.
- [ ] Перед активацией: гарантировать один submit одновременно на HH account (worker-level lock/DB lease).
- [ ] Перед активацией: runtime Pause/Resume/Stop-after-current + progress N/M/current/error/manual.
- [ ] Перед активацией: configurable safety interval.
- [ ] Bulk `Отправить выбранные` после bulk select.
- [ ] Отдельный retry/reset flow для `failed/manual_required` после ручного решения.

---

# Этап 8. Накопление правил

- [x] Сохраняется `user_decision_reason` для ручных отказов.
- [ ] LLM предлагает rule/change/no-generalization, но не активирует его.
- [ ] До approval показать влияние proposed rule на backlog.
- [ ] Version rules + selective rescore.
- [ ] Employer rejection не является preference signal пользователя.

---

# Этап 9. Версионирование кэшей

- [ ] Score cache: vacancy hash + resume/profile hash + rules version + scorer/model.
- [ ] Cover cache: vacancy hash + resume/profile hash + facts version + writer prompt/model.
- [ ] Context changes mark dependent result stale.

---

# Этап 10. Single-user + установка одной командой

- [ ] `install.sh`, Ubuntu 24.04.
- [ ] Docker/Compose bootstrap.
- [ ] Clone/update `gest0r1/Otclick-hh`.
- [ ] Existing `infra/bootstrap.py`, без ротации secrets при rerun.
- [ ] Compose + migrations + healthcheck.
- [ ] First user once, затем signup closed.
- [ ] HTTPS/domain для телефона.
- [ ] Install log + URL.
- [ ] Update: DB backup -> pull -> migrations -> healthcheck -> rollback instructions.

Планируемая команда:

```bash
curl -fsSL https://raw.githubusercontent.com/gest0r1/Otclick-hh/main/install.sh -o /tmp/otclick-install.sh && bash /tmp/otclick-install.sh
```

---

# Этап 11. Калибровка 20–30 вакансий

- [ ] Hard-filter precision.
- [ ] Score vs фактическое решение пользователя.
- [ ] Rejection reasons / proposed rules.
- [ ] 20–30 cover letters.
- [ ] Mobile UX.
- [ ] Только явно approved real sends.
- [ ] Restart/retry/idempotency.

---

# Этап 12. Auto mode — отдельным решением после калибровки

Не входит в обязательный MVP.

- [ ] minimum score;
- [ ] allowed hard conditions;
- [ ] daily limit;
- [ ] max consecutive errors;
- [ ] kill switch;
- [ ] forms/unknown required fields manual.

---

## Следующий порядок работ

1. Не активируя sender: довести persistent queue до retry/reset/progress semantics и single-account lock design.
2. Реализовать накопление правил из `user_decision_reason` с обязательным approval и impact preview.
3. Versioned score/cover context fingerprints и selective rescore/regenerate.
4. `install.sh` + безопасный update/backup/migrations/healthcheck.
5. Отдельно восстановить CI: workflow есть и trigger расширен, но GitHub возвращает `0 workflow_runs` даже на `feature/**` push.
6. Через несколько циклов/при готовности пользователя проверить web-session автопоиски на живом аккаунте и зафиксировать заполненный `/applicant/autosearch.xml` contract.
7. Только после отдельного решения пользователя — подключать sender engine к runtime и тестировать реальные HH sends.
