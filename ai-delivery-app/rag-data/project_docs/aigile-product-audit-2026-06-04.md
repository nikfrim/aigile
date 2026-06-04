# AIGILE Product Audit

Дата аудита: 2026-06-04  
Версия: 0.1.1-dev  
Цель: честно оценить, насколько текущие доработки выглядят как продукт, а не как недостоверная витрина для public GitHub.

## Итоговая оценка

AIGILE выглядит как честный локальный MVP / demo-ready prototype для Product / Delivery Manager кейса.

Сильная сторона проекта: есть живой end-to-end контур, реальные локальные сервисы, интеграция Plane + Mattermost + Ollama + RAG, и понятный управленческий сценарий вокруг AI Review, task chat и delivery intelligence.

Главный риск восприятия: часть руководительской аналитики является demo/heuristic layer. Это нормально для MVP, но это нужно явно писать. Нельзя утверждать, что Health Index, schedule confidence и flow metrics являются промышленной прогнозной моделью. Сейчас это explainable attention indicators для демо и интервью.

## Проверено вживую

Проверка выполнена локально 2026-06-04.

- Docker services running: Plane, Mattermost, n8n, Ollama, Open WebUI, Qdrant, RAG backend, AIGILE backend.
- `http://127.0.0.1:8091/health` вернул `ok: true`.
- `http://127.0.0.1:8092/health` вернул `ok: true`, `qdrant: true`.
- `http://127.0.0.1:8091/api/daily-delivery-brief` вернул Daily Brief с Health Index `60/100`.
- `http://127.0.0.1:8091/api/delivery-intelligence` вернул delivery health, review counts и delivery signals.
- `http://127.0.0.1:8092/rag/query` нашел проектную документацию в `project_docs`.
- Backend unit tests: `48` тестов, результат `OK`.

Наблюдение: в тестовом выводе есть warning про старую `AIGILE-1`, которой уже нет. Это не ломает тесты, но стоит считать housekeeping debt для runtime logs / stale context.

## Матрица зрелости

Легенда:

- `Working MVP` — работает локально и может быть показано как настоящая функция MVP.
- `Demo-ready` — хорошо подходит для интервью/демо, но использует упрощения или seeded runtime data.
- `Prototype` — полезно как направление, но требует усиления перед серьезным использованием.
- `Manual/Config` — функция существует, но зависит от ручной настройки.

| Доработка | Статус | Продуктовая оценка | Что честно говорить |
|---|---:|---|---|
| Docker local runtime | Working MVP | Хорошая основа локальной AI delivery platform. | Все сервисы поднимаются локально через Docker Desktop; это не облачный SaaS. |
| Health Dashboard | Working MVP | Полезный pre-demo / pre-smoke экран. | Показывает готовность инфраструктуры, а не качество delivery. |
| Plane structure | Manual/Config | Plane используется как source of truth, типы задач реализованы через labels. | В Plane Community нет enterprise work item types, поэтому типы сделаны метками. |
| AI Review Gate | Working MVP | Одна из самых сильных фич продукта. | Кнопка запускает локальный AI review, агенты выбираются по type label, результат сохраняется. |
| Agent Router | Working MVP | Правильный продуктовый слой поверх Plane labels. | Система намеренно блокирует анализ без метки типа, чтобы не выбирать агентов вслепую. |
| Deterministic quality gate | Working MVP | Хорошее усиление доверия: пустая задача не должна получать green. | До LLM есть простые правила качества: title-only, missing description, missing AC. |
| AI-R / AI-A labels | Working MVP with known fragility | Идея сильная: видно, где AI участвовал. | Работоспособность зависит от Plane label API и существования label; стоит усилить тестами. |
| AI comments instead of overwriting description | Working MVP | Хорошее safety-решение. | AI не перетирает пользовательское описание; предложения уходят в комментарии или в AC по подтверждению. |
| Controlled Apply Flow | Working MVP / partial | Безопасная модель: сначала draft, потом `y/да`. | Пока это не полноценный diff editor; это MVP approval flow. |
| Mattermost Task Chat | Working MVP | Сильная demo-фича: агент общается в thread по задаче. | Агент держит контекст задачи и отвечает в thread; изменения в Plane только после approval. |
| Task Context Graph | Working MVP | Важное отличие от обычного чат-бота. | Агент подтягивает родителя, связи, модуль, цикл и последний AI review. |
| Delivery Signal commands | Working MVP | Хорошая delivery-management фича. | Команды `!risk`, `!blocker`, `!dep`, `!decision`, `!question`, `!action` создают сигналы. |
| Automatic meeting/thread extraction | Prototype | Пока лучше не продавать как fully automatic. | MVP делает command-based capture; AI extraction можно развивать следующим этапом. |
| Delivery Intelligence Dashboard | Demo-ready | Хорошо показывает идею центра управления delivery. | Это управленческий экран на базе доступных сигналов, но не BI-система. |
| Daily Delivery Brief | Demo-ready | Очень полезно для интервью: executive summary, risks, decisions, actions. | Brief не выдумывает факты, но demo data может быть seeded. |
| Health Index | Demo-ready heuristic | Хороший индикатор внимания, не научная метрика. | Score объяснимый: blockers, red/yellow reviews, weak requirements, open signals, unreviewed work. |
| Kanban / flow metrics | Demo-ready heuristic | Визуально полезно, но требует настоящей истории для production. | Throughput, lead time, WIP, aging и rework сейчас демонстрационные/выводимые из доступных данных. |
| Demo Seed / Reset | Working MVP | Сильная фича для интервью. | Seed/reset работает только с `[DEMO]` и `AIGILE-DEMO`, обычные задачи не трогает. |
| RAG Backend + Qdrant | Working MVP | Хорошая техническая база. | Локальный RAG с отдельными коллекциями, без cloud API. |
| RAG collections separation | Working MVP | Архитектурно правильно. | Книги, проектные документы, Plane Pages, decisions и prompts не смешаны. |
| Mattermost KB upload + `/kb` | Working MVP | Удобный интерфейс загрузки знаний. | Работает для configured channel; duplicate handling есть; UX требует дальнейшей полировки. |
| PDF ingestion | Working MVP / limited | Достаточно для MVP. | Поддерживается text PDF; сканы/OCR и сложная разметка не гарантируются. |
| Plane Pages → RAG | Working MVP | Сильная идея: approved docs из Plane становятся knowledge. | Индексируются только Public Pages с `[AI]`, отдельная коллекция `plane_pages`. |
| n8n workflows | Working MVP / integration layer | Полезно как orchestration слой. | Старый flow сохранен, RAG workflow добавлен; не надо утверждать, что все сценарии полностью production hardened. |
| Notion documentation | Documentation layer | Хорошо для product story и портфолио. | Notion не runtime component; это база знаний/документация. |

## Что можно уверенно показывать

1. Health Dashboard: подтверждает, что локальная инфраструктура жива.
2. Plane task: пользователь создает задачу и ставит type label.
3. AI Review Gate: агентный review со светофором и детальными замечаниями.
4. Mattermost Task Chat: отправка задачи в thread и разговор с агентом по контексту задачи.
5. Approval Flow: агент готовит изменение, пользователь подтверждает `y/да`, изменение попадает в Plane безопасно.
6. RAG `/kb`: вопрос по загруженным знаниям.
7. Daily Delivery Brief: управленческий экран с risks, signals, Health Index и suggested actions.

## Что нельзя преувеличивать

- Это не production-ready enterprise platform.
- Это не замена Jira Advanced Roadmaps, Linear или полноценного BI.
- Health Index не является статистически валидированной моделью прогноза сроков.
- Kanban flow metrics пока не основаны на полноценной исторической event-store модели.
- Plane UI patching не является официальным plugin API и может требовать поддержки после обновлений Plane.
- Security/RBAC сделаны на уровне локального MVP, не enterprise-grade.
- LLM output требует human review; AI не является финальным decision maker.

## Public GitHub risks

Текущее состояние в целом безопасно для public GitHub при условии, что `.env`, runtime logs и локальные secrets не коммитятся.

Проверено:

- `.env` не отслеживается git.
- `ai-delivery-app/logs/*` не отслеживаются git.
- Runtime demo data хранится локально и не является частью публичного репозитория.

Рекомендации:

- Держать в README секцию `Product Maturity`.
- Не писать в публичном описании `production-ready`.
- Использовать формулировки `local MVP`, `demo-ready`, `self-hosted prototype`.
- Для Daily Brief явно писать: `explainable management indicator, not formal forecast`.
- Перед push проверять, что `.env`, токены Mattermost/n8n и export-файлы с секретами не попали в git.

## Product credibility score

Оценка как портфолио-кейс Product Delivery Manager: `8/10`.

Почему высоко:

- Есть реальная локальная интеграция нескольких систем.
- Есть понятная бизнес-цель: улучшить delivery visibility и качество задач.
- Есть human-in-the-loop модель, а не безответственная AI-автоматизация.
- Есть RAG, task memory, agent review, delivery signals и executive brief.
- Есть demo seed/reset и smoke-test подход.

Почему не 10:

- UI patching Plane хрупкий.
- Flow metrics пока demo/heuristic.
- Нужна явная event history для настоящей динамики.
- Нужна лучше формализованная безопасность и user mapping.
- Нужны e2e тесты на критические flows Plane → Mattermost → Plane.

## Рекомендации перед собеседованием

1. Начинать с честной формулировки:
   `Это локальный MVP, который демонстрирует, как AI может помогать Delivery Manager снижать хаос в задачах, обсуждениях и управленческих сигналах.`

2. Показывать сценарий, а не набор технологий:
   `Задача в Plane → AI Review → обсуждение в Mattermost thread → delivery signals → Daily Brief для руководителя.`

3. Не прятать ограничения:
   `Flow metrics сейчас demo-calibrated; следующий шаг — event store и реальные исторические snapshots.`

4. Подчеркнуть роль человека:
   `AI предлагает, человек подтверждает. Система не меняет задачу без approval.`

5. Сделать акцент на процессе:
   `AIGILE помогает команде быстрее находить неготовые требования, блокеры, риски и решения, которые требуют внимания.`

## Следующие hardening steps

1. Добавить real event store для истории статусов, AI review, signals и task changes.
2. Перевести flow metrics с demo heuristics на расчет по snapshot history.
3. Добавить e2e smoke runner: Plane issue → AI Review → Mattermost thread → approval → Plane comment/label.
4. Усилить label API: отдельная диагностика, почему label не создан/не применен.
5. Добавить export-safe sample `.env.example`.
6. Добавить публичный `docs/demo-scope.md` с честным описанием demo limitations.
7. Добавить screenshots/demo script для interview flow.

## Вывод

Проект не выглядит шарлатанским, если правильно его позиционировать.

Правильное позиционирование:

`AIGILE is a local AI-assisted delivery MVP that demonstrates how AI agents, task context, RAG knowledge, and team discussion signals can improve product delivery visibility.`

Неправильное позиционирование:

`AIGILE is a production-ready AI delivery operating system with accurate project forecasting.`

Сейчас это сильный портфолио-кейс: живой локальный MVP с понятной управленческой ценностью и честными границами зрелости.
