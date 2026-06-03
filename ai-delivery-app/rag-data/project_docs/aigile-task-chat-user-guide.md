# AIGILE Task Chat User Guide

Version: 0.1.1-dev
Updated: 2026-06-03

## Назначение

Task Chat позволяет обсуждать конкретную задачу Plane с `aigile-agent` в треде Mattermost.

Первое сообщение агента по задаче становится корнем треда. Все вопросы, уточнения, риски и договоренности по задаче лучше писать ответами именно в этот тред.

## Базовый сценарий

1. Открой задачу в Plane.
2. Нажми `AI анализ`.
3. После анализа нажми `В Mattermost`.
4. Открой тред сообщения от `aigile-agent`.
5. Задавай вопросы по задаче или проси подготовить изменения.
6. Если агент подготовил черновик изменения Plane, подтверди или отклони его.

## Что агент помнит по задаче

Task Chat хранит контекст конкретной задачи:

- issue key и название;
- описание;
- метки и тип задачи;
- статус;
- родительский Epic;
- дочерние задачи;
- связанные задачи;
- модуль;
- цикл;
- последний AI Review;
- историю диалога в треде.

## Команды подтверждения

Используются короткие ответы:

- `y` или `да` - подтвердить и применить ожидающий черновик;
- `n` или `нет` - отклонить и удалить ожидающий черновик.

Агент не должен менять Plane без явного `y` или `да`.

## Служебные команды

### `!help`

Показывает краткую памятку по командам прямо в треде Mattermost.

### `!status`

Показывает, что агент сейчас помнит по задаче:

- ключ и название задачи;
- тип;
- последний AI Review status;
- количество родительских, дочерних и связанных задач;
- количество модулей и циклов;
- есть ли ожидающий черновик.

## Команды изменения Plane

Эти команды создают черновик изменения. Plane меняется только после подтверждения `y` или `да`.

### `!ac`

Подготовить Acceptance Criteria.

Пример:

```text
!ac пользователь видит понятную ошибку при вводе слабого пароля
```

После подтверждения агент:

- находит или создает блок `Acceptance Criteria` в описании задачи;
- добавляет строку с пометкой `[AI]`;
- ставит метку `AIA`;
- оставляет короткий комментарий-резюме.

Если агент только что предложил acceptance criteria, можно написать:

```text
добавь их в задачу
```

Система возьмет предыдущие предложенные критерии, а не буквальный текст команды.

### `!note`

Подготовить заметку в комментарий Plane.

Пример:

```text
!note проверить UX-текст ошибки вместе с frontend
```

### `!deadline`

Подготовить заметку о сроке в комментарий Plane.

Пример:

```text
!deadline нужно завершить до 16 июня 2026
```

## Delivery Signal команды

Эти команды не создают черновик Plane и не меняют задачу.
Они сохраняют управленческие сигналы для Delivery Intelligence Dashboard и Daily Delivery Brief.

### `!risk`

Создать delivery risk.

Пример:

```text
!risk high есть риск не успеть к демо
```

Severity можно указать вторым словом:

- `low`;
- `medium`;
- `high`;
- `critical`.

Если severity не указана, используется `medium`.

### `!blocker`

Создать blocker. По умолчанию severity = `critical`.

Пример:

```text
!blocker нет доступа к платежному стенду
```

### `!dep`

Создать dependency.

Пример:

```text
!dep ждем backend endpoint для проверки пароля
```

### `!decision`

Зафиксировать решение или решение, которое нужно принять.

Пример:

```text
!decision для MVP используем вариант B
```

### `!question`

Создать открытый вопрос.

Пример:

```text
!question кто владелец финального текста ошибки?
```

### `!action`

Создать action item.

Пример:

```text
!action назначить QA owner перед release
```

## Где видны delivery signals

Delivery signals отображаются в:

- `http://localhost:8091/delivery-dashboard`;
- `http://localhost:8091/daily-delivery-brief`;
- `http://localhost:8091/api/delivery-signals`;
- `http://localhost:8091/api/daily-delivery-brief`.

Сигналы хранятся отдельно от Plane comments:

```text
ai-delivery-app/logs/delivery-signals.jsonl
```

Статусы сигналов:

- `open`;
- `acknowledged`;
- `resolved`.

## Daily Delivery Brief

Daily Delivery Brief превращает данные из Plane, AI Review history и Mattermost delivery signals в короткую управленческую сводку.

Страница:

```text
http://localhost:8091/daily-delivery-brief
```

JSON:

```text
http://localhost:8091/api/daily-delivery-brief
```

Brief показывает:

- overall status;
- executive summary;
- top risks;
- blockers;
- decisions needed;
- requirement quality issues;
- changes since yesterday;
- suggested actions.

Brief не выдумывает факты. Если данных нет, он пишет это явно.

## Safety Rules

- Drafts are not applied automatically.
- Approved `!ac` drafts update only the `Acceptance Criteria` block in the task description.
- The added AC line is marked with `[AI]`.
- Approved `!ac` drafts add label `AIA`.
- Other approved drafts are added as Plane comments and add label `AI-A`.
- Delivery signal commands are stored separately from Plane comments.
- Dialogue memory is stored per Mattermost thread.
- The agent uses task context: parent epic, linked tasks, module, cycle, and latest AI review.
