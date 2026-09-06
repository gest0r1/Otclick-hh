# Otclick-hh — план реализации персонального приложения откликов

Обновлено: 2026-09-07

## Статус

- [x] Проверена исходная архитектура `gest0r1/Otclick-hh`.
- [x] Разобран `gest0r1/hh-auto`: durable queue, BatchManager, ручной выбор, последовательный apply-loop, подход к сопроводительным.
- [x] Исследованы дополнительные HH-проекты и варианты источников вакансий.
- [x] Принято решение: основной объект — `Search Source` с собственным cursor; нативный HH автопоиск используется как предпочтительный способ импорта/настройки, но runtime не зависит от недокументированного endpoint.
- [x] Подписки на отдельные компании исключены из продукта по решению пользователя.
- [x] Реализация начата в ветке `feature/persistent-vacancy-funnel`.
- [x] `0b5ad962` — persistent Search Sources + схема vacancy funnel + URL parser.
- [x] `fe1df614` — `ALLOW_REAL_APPLY=false` и общий backend kill-switch для реальных HH submit.

> После каждого завершённого этапа отмечать `[x]`, фиксировать результат и commit SHA.

---

## Архитектурный принцип

Не переносить `hh-auto` целиком и не заменять Otclick его Playwright-архитектурой.

Используем:

- **Otclick:** web-session HH, FastAPI, PostgreSQL/Supabase, существующая авторизация, формы, Docker/frontend.
- **hh-auto:** разделение discovery и user review, lifecycle queue, последовательная обработка, проверенный подход к personalized cover letters.

Целевой поток:

`HH Search Sources -> PostgreSQL vacancy_pipeline -> hard filter -> LLM score -> mobile review -> selected -> personalized draft -> approval -> persistent send queue -> sequential HH submit -> verification -> applications log`

Поиск/скоринг физически не имеют права отправлять отклик.

---

# План реализации

## Этап 0. Safety и baseline

- [x] Создать рабочую ветку `feature/persistent-vacancy-funnel`.
- [ ] Зафиксировать baseline backend/frontend/build через CI.
- [x] Добавить `ALLOW_REAL_APPLY=false` по умолчанию.
- [x] Поставить kill-switch в общем низкоуровневом `form_filler.submit_response`, до загрузки HH-сессии и любого POST.
- [x] Тем же guard закрыть `submit_prepared_form` для анкет.
- [x] Добавить тесты: при выключенном флаге HH-сессия даже не открывается.
- [ ] Добавить интеграционный тест новой модели: вакансия без approved send-job никогда не отправляется.

**Приёмка:** discovery/scoring/review можно разрабатывать и запускать, не рискуя реальным откликом.

---

## Этап 1. Данные кандидата

Источники:

- `01_Карьерное_позиционирование_CIO_CDTO(1).md` -> критерии поиска/оценки;
- `02_RFL_банк_достижений_и_фактов(1).md` -> подтверждённые facts/cases.

- [ ] Подготовить структурированный `candidate_profile`.
- [ ] Подготовить подтверждённые facts/cases.
- [ ] Не импортировать `нужно уточнить`, гипотезы и запрещённые формулировки как факты.
- [ ] Сделать deterministic loader prepared data в БД.
- [ ] Показывать в UI критерии и факты, реально используемые моделью.

**Решение:** универсальный Markdown parser не делать.

---

## Этап 2. Постоянная воронка вакансий

- [x] Добавить миграцию `034_vacancy_pipeline.sql`.
- [x] Добавить `vacancy_pipeline` с lifecycle статусами и unique `(user_id, hh_vacancy_id)`.
- [x] Добавить `vacancy_search_sources`.
- [x] Добавить many-to-many `vacancy_pipeline_sources` — одна вакансия может прийти из нескольких источников.
- [x] Заложить хранение title/employer/area/salary/published/full description/raw snapshot/score/reasons.
- [x] Добавить persistence helper с dedup без сброса уже принятого user decision.
- [x] Добавить conditional/atomic lifecycle transition helper.
- [ ] Перевести producer с in-memory `asyncio.Queue` на запись в `vacancy_pipeline` как source of truth.
- [ ] Убрать найденные вакансии из старого auto-apply runner; discovery и send должны стать разными worker-путями.
- [ ] Добавить restart/recovery tests.

**Приёмка:** найденные вакансии и lifecycle полностью переживают рестарт; discovery не запускает apply.

---

## Этап 3. Источники вакансий

### Модель Search Source

Каждое направление поиска — постоянный объект:

- имя (`CIO Москва`, `CDTO`, `CEO IT` и т.п.);
- origin/source type;
- параметры HH;
- resume/profile binding при необходимости;
- `cursor` / последнее успешно обработанное состояние;
- enabled/disabled;
- last check / error / статистика.

**Подписки на отдельные компании не реализуем.**

### 3.1. HH search URL

- [x] Backend parser принимает `hh.ru`, regional `*.hh.ru`, `hh.kz`.
- [x] Повторяющиеся параметры (`area`, `search_field`, `professional_role` и др.) не теряются: хранятся ordered query pairs.
- [x] Неизвестные параметры не выбрасываются и отдельно помечаются как unsupported.
- [ ] API preview URL перед сохранением.
- [ ] API CRUD Search Sources.
- [ ] Mobile UI: вставить URL → увидеть распознанные/неизвестные параметры → сохранить.

### 3.2. Нативные автопоиски / подписки HH

Предпочтительный UX настройки, но не runtime dependency.

- [ ] Проверить authenticated HH page/internal flow списка автопоисков.
- [ ] Если flow стабилен — импортировать сохранённые критерии в `vacancy_search_sources` с `source_type=hh_autosearch`.
- [ ] После импорта выполнять поиск самостоятельно по сохранённым параметрам и нашему cursor.
- [ ] Если внутренний flow HH изменился/недоступен — импорт помечается ошибкой; ручной Search URL остаётся рабочим и MVP не блокируется.

### 3.3. Incremental ingestion

- [ ] На первом запуске source брать ограниченное окно свежих вакансий, а не весь исторический SERP.
- [ ] После успешного запуска двигать собственный cursor.
- [ ] На следующих циклах забирать только свежий overlap-window для защиты от пропусков + DB dedup.
- [ ] Не обходить 20 страниц каждого source каждые несколько минут без необходимости.
- [ ] Вести статистику source: fetched / new / duplicate / filtered / score_error.

### 3.4. HH рекомендации

Только дополнительный discovery-канал.

- [ ] Проверить реальный authenticated endpoint/flow.
- [ ] Если стабилен — источник `recommendations` с небольшим лимитом за цикл/сутки.
- [ ] Всё из рекомендаций проходит те же hard filter + LLM score.
- [ ] Если endpoint нестабилен — source отключён, MVP не зависит от него.

**Приёмка:** основная работа приложения не зависит ни от рекомендаций, ни от undocumented auto-search endpoint.

---

## Этап 4. Hard filter и LLM scoring

### Hard filter

- [ ] Стоп-слова.
- [ ] Явно исключённые компании.
- [ ] Подтверждённые исключённые отрасли/типы бизнеса.
- [ ] Для каждого исключения хранить конкретную причину.
- [ ] Неизвестная отрасль/выручка/зарплата не является автоматическим отказом.

### LLM score

- [ ] Передавать полное описание вакансии.
- [ ] Score 0–100.
- [ ] Отдельные компоненты: роль, масштаб, transformation mandate, отрасль/business context.
- [ ] Сохранять `pros`, `risks`, `unknowns`, `confidence`.
- [ ] Различать CIO transformation vs эксплуатационный IT head; CTO/platform leadership vs lead developer; standalone business vs функция холдинга.
- [ ] Ошибка LLM -> `score_error`, не fail-open.
- [ ] Низкий score остаётся видимым пользователю.

---

## Этап 5. Экран `Vacancies` / review

- [ ] Отдельная страница от `Applications`.
- [ ] Desktop — таблица; mobile — карточки.
- [ ] Компания, роль, зарплата, score, source(s), статус.
- [ ] Полное описание + объяснение score.
- [ ] `Выбрать`, `Отклонить`, `Отложить`.
- [ ] Причина отклонения.
- [ ] Bulk select.
- [ ] Фильтры по lifecycle.

**Приёмка:** сценарий `показать список -> пользователь выбирает` работает с телефона и переживает рестарт.

---

## Этап 6. Сопроводительные письма

- [ ] Адаптировать принципы `hh-auto/prompts/cover_letter.md`.
- [ ] Генерировать письмо для каждой выбранной вакансии, даже если HH его формально не требует.
- [ ] Контекст: выбранное HH resume + релевантные подтверждённые facts/cases + full vacancy + scoring anchors.
- [ ] Writer не получает numeric score как аргумент убеждения.
- [ ] Только подтверждённые факты; никаких придуманных/округлённых метрик.
- [ ] 500–750 символов, язык вакансии, конкретный близкий кейс, 1–2 evidence, role/employer-specific close.
- [ ] Draft сохраняется в БД.
- [ ] Mobile edit.
- [ ] Approve сохраняет hash точного утверждённого текста.
- [ ] Любое изменение после approve сбрасывает approval.
- [ ] Ошибка LLM не создаёт auto-sendable fallback.

---

## Этап 7. Постоянная очередь отправки

Отдельная `application_send_queue`; не `asyncio.Queue`.

- [ ] В очередь входит только approved vacancy/letter.
- [ ] Bulk `Отправить выбранные`.
- [ ] Строго один submit одновременно на HH account.
- [ ] Проверка vacancy alive + already responded перед submit.
- [ ] Network uncertainty -> сначала reconciliation с HH, потом retry decision.
- [ ] Unique/idempotency vacancy+resume.
- [ ] Не blacklist работодателя целиком из-за одного старого отклика.
- [ ] Анкеты/неизвестные обязательные поля -> manual review.
- [ ] Небольшой configurable safety interval; не переносить обязательные 15–25 секунд `hh-auto`.
- [ ] Progress `N/M`, current, sent/error/manual.
- [ ] Pause/Resume/Stop after current.

---

## Этап 8. Накопление правил

- [ ] Сохранять user rejection reason.
- [ ] LLM может предложить stop-word/company/content rule/change/no-generalization.
- [ ] Rule proposal не действует до явного подтверждения.
- [ ] Перед подтверждением показать влияние на текущий backlog.
- [ ] Версионировать rules.
- [ ] Selective rescore только непросмотренных затронутых вакансий.
- [ ] Отказ работодателя не является negative preference signal пользователя.

---

## Этап 9. Версионирование кэшей

- [ ] Score cache: vacancy content hash + resume/profile hash + rules version + scorer/model.
- [ ] Cover cache: vacancy content hash + resume/profile hash + facts version + writer prompt/model.
- [ ] Изменение контекста помечает зависимые результаты stale.
- [ ] Не пересчитывать неизменившиеся данные.

---

## Этап 10. Single-user + one-command install

- [ ] `install.sh` в корне.
- [ ] Ubuntu 24.04.
- [ ] Docker/Compose при отсутствии.
- [ ] Clone/update `gest0r1/Otclick-hh`.
- [ ] Использовать `infra/bootstrap.py`; повторный запуск не вращает secrets.
- [ ] Compose + migrations + healthcheck.
- [ ] Первый пользователь создаётся один раз; затем публичная signup закрыта.
- [ ] HTTPS/domain для телефона.
- [ ] Install log + URL.
- [ ] Update: DB backup -> pull -> migrations -> healthcheck -> rollback instructions.

Планируемая команда:

```bash
curl -fsSL https://raw.githubusercontent.com/gest0r1/Otclick-hh/main/install.sh -o /tmp/otclick-install.sh && bash /tmp/otclick-install.sh
```

---

## Этап 11. Калибровка 20–30 вакансий

- [ ] Precision hard filters.
- [ ] Score vs фактический выбор пользователя.
- [ ] Rejection reasons / rule proposals.
- [ ] Качество 20–30 писем.
- [ ] Mobile UX.
- [ ] Только явно approved реальные sends.
- [ ] Restart/retry/idempotency.

---

## Этап 12. Auto mode — только отдельным решением

Не входит в обязательный MVP.

- [ ] minimum score;
- [ ] allowed hard conditions;
- [ ] daily limit;
- [ ] max consecutive errors;
- [ ] kill switch;
- [ ] анкеты/unknown required fields всегда manual.

---

## Ближайший порядок работ

1. CI baseline + safety tests.
2. Перевести producer на persistent discovery-only pipeline.
3. Разделить discovery worker и старый apply runner.
4. Candidate profile/facts prepared data.
5. Search Source API + incremental cursor ingestion.
6. Native HH auto-search import probe.
7. Full-text scoring.
8. Mobile review UI.
9. Cover letter draft/approval.
10. Persistent sequential send queue.
11. Rules/caches/install/calibration.
