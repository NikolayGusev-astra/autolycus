#!/usr/bin/env python3
"""
Telegram chat JSON export - извлечение проблем/косяков из мотопереписки.
3 прохода: (1) ID отбор по ключам (2) извлечение контекста (3) категоризация+отчёт
"""

import json, re, sys, os
from collections import Counter, defaultdict

# --- Pass 1: проблемные паттерны (разбиты на группы для читаемости) ---
PATTERNS = re.compile(
    r'\b(?:сломал[а-я]?|поломк[а-я]?|проблем[а-я]?|косяк[а-я]?|дефект[а-я]?'
    r'|неисправн[а-я]?|отказ[а-я]?|брак[а-я]?'
    r'|треснул[а-я]?|течёт|прот[её]к[а-я]?|развалил[а-я]?|лопнул[а-я]?'
    r'|не\s*работа(?:е?т)?|не\s*завод[ии]т'
    r'|не\s*включа[её]тся|не\s*стартует|не\s*крутит|не\s*ед[её]т'
    r'|баг[а-я]?|глюк[а-я]?|ошибк[а-я]?|трабл[а-я]?'
    r'|ремонтир[а-я]?|замен[яи]л[а-я]?|помен[яя]л[а-я]?|восстанов[ии]л[а-я]?'
    r'|авари[яйюе]?|дтп|врезал[а-я]?|упал[а-я]?|падени[яй]'
    r'|занос[а-я]?|занесл[а-я]?'
    r'|помогит[еь]|подскажит[еь]|что\s+делать|как\s+быть'
    r'|стучит|скрипит|свистит'
    r'|вибраци[яй]|вибрирует|гре[её]тся|перегр[её]в'
    r'|дымит|воня[её]т|нагар[а-я]?'
    r'|плохо\s+работ(?:ае?т)?|тупит|тормоз[ии]т|заедае[тт]'
    r'|заклини[лв]|хренов[а-я]|фигов[а-я]|дерьмов[аьмов[а-я]'
    r'|цеп[ьь]\s*звенит|подшипник[а-я]?\s*гул'
    r'|компресси[яй]|сапун|нет\s+искры'
    r'|антифриз|тосол|масло[оо]жр[её]т|маслож[оо]р'
    r'|расход\s*масла|бензин\s*жр[её]т'
    r'|гаранти[яй]|сервис[а-я]|качеств[а-я]|сборк[а-я])',
    re.IGNORECASE
)

# --- Pass 3: категоризация ---
TOPIC_MAP = {
    'engine': r'двигател|мотор|движок|цилиндр|поршн|коленвал|шатун|клапан|гбц|головк|прокладк|кольц|распредвал|гильз|поршнев|компресси',
    'electrics': r'электрик|электрон|провод|аккумулятор|аккум|генератор|статор|ротор|реле|катушк|зажигани|свеч|коммутатор|бензонасос|эбу|мозг|датчик|сигнализаци|инжектор|карбюратор|иммо|абс',
    'fuel': r'топлив|бензин|карбюратор|жиклер|жиклёр|поплав|форсунк|дроссел|впуск|воздухан|коллектор|фильтр возд',
    'brakes': r'тормоз|колодк|суппорт|тормозух|гидравлик',
    'suspension': r'подвеск|амортизатор|вилк|маятник|моноаморт|пружин|сайлент|втулк|шток',
    'transmission': r'трансмисси|коробк|вариатор|кпп|сцеплени|привод|кардан|редуктор|цеп|зв[её]зд|шлиц|натяжител',
    'cooling': r'охлаждени|радиатор|вентилятор|антифриз|тосол|помп|термостат|перегр|кипит',
    'exhaust': r'выхлоп|глушител|глушак|резонатор|катализатор|пламягасител|выпускн',
    'frame_body': r'рам[а-я]|пластик|обвес|сидени|седл|бак|крыл[а-я]|фар[а-я]|оптик|поворотник|зеркал|приборн|панел|экран|диспле',
    'tires': r'шин[а-я]|колес|камер|ниппел|балансировк|прокол',
    'oil_fluids': r'масл|жидкост|тормозух',
    'starter_battery': r'стартер|кик[сш]|акб|аккумулятор|прикури',
    'assembly_hq': r'качеств\s*сборк|заводск\s*дефект|люфт|зазор|хлам|гавн',
    'general_trouble': r'гаранти|сервис\s*центр|дилер|официал|обращени\s*по\s*гаранти',
}


def extract_text(msg):
    t = msg.get('text', '')
    if isinstance(t, list):
        return ''.join(
            item if isinstance(item, str) else item.get('text', '')
            for item in t
        )
    return t or ''


def classify_topic(text):
    text_lower = text.lower()
    result = []
    for name, pattern in TOPIC_MAP.items():
        if re.search(pattern, text_lower):
            result.append(name)
    return result or ['other']


def make_fingerprint(text, length=120):
    clean = re.sub(r'\s+', ' ', text[:300]).strip().lower()
    return clean[:length]


# ===================== PASS 1: отбор =====================
def pass1_scan(messages, limit=None):
    """Сканирование + поиск по ключам, возвращает индексы проблемных"""
    matched = set()
    total = len(messages)
    batch = max(1, total // 50)

    for i, msg in enumerate(messages):
        if limit and i >= limit:
            break
        if msg.get('type') != 'message':
            continue
        text = extract_text(msg)
        if len(text) < 4:
            continue
        if PATTERNS.search(text):
            matched.add(i)
        if i % batch == 0:
            continue
        pct = i * 100 // max(total, 1)
        print(f"  [{pct}%] {i}/{total} найдено={len(matched)}", file=sys.stderr)

    return sorted(matched)


# ===================== PASS 2: контекст =====================
def pass2_extract(messages, indices, ctx_window=1, limit=None):
    """Извлекает окна с контекстом вокруг проблемных сообщений"""
    total = len(messages)
    result = []
    seen_ids = set()

    for idx in indices:
        if limit and len(result) >= limit:
            break
        start = max(0, idx - ctx_window)
        end = min(total - 1, idx + ctx_window)
        window = []

        for j in range(start, end + 1):
            m = messages[j]
            mid = m.get('id', j)
            if mid in seen_ids and j != idx:
                continue
            seen_ids.add(mid)
            text = extract_text(m)
            window.append({
                'msg_id': m.get('id', j),
                'type': m.get('type', 'unknown'),
                'date': m.get('date', ''),
                'sender': m.get('from', m.get('actor', 'unknown')),
                'text': text[:500],
                'is_match': j == idx,
            })

        if window:
            result.append({
                'match_idx': idx,
                'match_msg_id': messages[idx].get('id', idx),
                'window': window,
            })

    return result


# ===================== PASS 3: отчёт =====================
def pass3_report(problems, total_msgs, chat_name):
    topic_counts = Counter()
    topic_uniq = defaultdict(set)  # topic -> set of fingerprints
    sender_set = set()
    all_by_topic = defaultdict(list)

    for p in problems:
        # Собираем текст из проблемного сообщения (первое с is_match=True)
        match_text = ''
        for w in p.get('window', []):
            if w.get('is_match'):
                match_text = w.get('text', '')
                sender_set.add(w.get('sender', '?'))
                break

        if not match_text:
            continue

        topics = classify_topic(match_text)
        fp = make_fingerprint(match_text)

        for t in topics:
            topic_counts[t] += 1
            topic_uniq[t].add(fp)
            all_by_topic[t].append(p)

    # --- Формируем отчёт ---
    lines = []
    lines.append(f"=== Анализ чата: {chat_name} ===")
    lines.append(f"Всего сообщений: {total_msgs}")
    lines.append(f"Проблемных: {len(problems)}")
    lines.append(f"Авторов: {len(sender_set)}")
    lines.append("")

    # Распределение
    lines.append("Категория | кол-во | % | уникальных проблем")
    lines.append("-" * 55)
    for t, c in topic_counts.most_common(20):
        uniq = len(topic_uniq[t])
        pct = c * 100 // max(1, len(problems))
        bar = '█' * min(c // 20, 40)
        lines.append(f"  {t:18s} | {c:5d} | {pct:2d}% | {uniq:3d} {bar}")

    lines.append(f"\n{'='*70}")
    lines.append("Проблемы по категориям (топ-10 уникальных на категорию):")
    lines.append(f"{'='*70}")

    for topic_name, count in topic_counts.most_common():
        ps = all_by_topic.get(topic_name, [])
        if not ps:
            continue
        lines.append(f"\n--- [{topic_name}] ({count} сообщ., {len(topic_uniq[topic_name])} унив.) ---")

        # Группируем по фингерпринту
        fp_groups = defaultdict(list)
        for p in ps:
            match_text = ''
            sender = '?'
            date = ''
            for w in p.get('window', []):
                if w.get('is_match'):
                    match_text = w.get('text', '')
                    sender = w.get('sender', '?')
                    date = w.get('date', '')
                    break
            if match_text:
                fp = make_fingerprint(match_text)
                fp_groups[fp].append((sender, date, match_text))

        # Сортируем группы по размеру
        sorted_groups = sorted(fp_groups.items(), key=lambda x: -len(x[1]))
        for i, (fp, group) in enumerate(sorted_groups[:10]):
            repeat = len(group)
            s, d, txt = group[0]
            lines.append(f"\n  {i+1}. [{s}] (x{repeat} если >1)")
            lines.append(f"     {txt[:250]}")
            if repeat > 3:
                dates = ', '.join(g[1][:10] for g in group[:3])
                lines.append(f"     повтор: {repeat}x, даты: {dates}...")

    lines.append(f"\n{'='*70}")
    lines.append(f"Всего: {len(problems)} проблемных сообщений с проблемами из {total_msgs}")
    lines.append("Для детального JSON-отчёта: запусти с --save-report")

    return '\n'.join(lines)


# ===================== MAIN =====================
def main():
    import argparse
    parser = argparse.ArgumentParser(description='telegram chat problem analyzer')
    parser.add_argument('file', help='result.json')
    parser.add_argument('--context', '-c', type=int, default=1, help='сообщ. контекста')
    parser.add_argument('--limit', type=int, help='лимит сообщений')
    parser.add_argument('--save-json', help='сохранить промежуточный JSON')
    parser.add_argument('--save-report', help='структурированный отчёт (JSON)')
    parser.add_argument('--load-json', help='загрузить промежуточный JSON вместо сканирования')
    parser.add_argument('--quiet', '-q', action='store_true', help='без прогресс-бара')
    args = parser.parse_args()

    if not args.load_json:
        if not args.file or not os.path.exists(args.file):
            print(f"Файл не найден: {args.file}")
            sys.exit(1)

        print(f"Загрузка {args.file}...", file=sys.stderr)
        with open(args.file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        messages = data['messages']
        total = len(messages)
        chat_name = data.get('name', '?')

        print(f"Pass 1: сканирование {total} сообщений...", file=sys.stderr)
        indices = pass1_scan(messages, limit=args.limit)
        print(f"Найдено проблемных: {len(indices)}", file=sys.stderr)

        print(f"Pass 2: извлечение контекста...", file=sys.stderr)
        problems = pass2_extract(messages, indices, ctx_window=args.context, limit=args.limit)

        if args.save_json:
            with open(args.save_json, 'w', encoding='utf-8') as f:
                json.dump({
                    'chat_name': chat_name,
                    'chat_id': data.get('id', 0),
                    'total_messages': total,
                    'problem_count': len(indices),
                    'problems': problems,
                }, f, ensure_ascii=False, indent=2)
            print(f"Промежуточный JSON: {args.save_json} ({os.path.getsize(args.save_json)//1024}KB)", file=sys.stderr)

        # Освобождаем память
        del data
        del messages

    else:
        with open(args.load_json, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        problems = loaded['problems']
        total = loaded.get('total_messages', '?')
        chat_name = loaded.get('chat_name', '?')
        print(f"Загружено {len(problems)} проблем из {args.load_json}", file=sys.stderr)

    print(f"Pass 3: категоризация и отчёт...", file=sys.stderr)
    report = pass3_report(problems, total, chat_name)
    print(report)

    if args.save_report:
        # Формируем структурированный отчёт для машинной обработки
        topic_counts = Counter()
        topic_uniq = defaultdict(set)
        for p in problems:
            match_text = ''
            for w in p.get('window', []):
                if w.get('is_match'):
                    match_text = w.get('text', '')
                    break
            if match_text:
                for t in classify_topic(match_text):
                    topic_counts[t] += 1
                    topic_uniq[t].add(make_fingerprint(match_text))

        report_json = {
            'chat_name': chat_name,
            'total_messages': total,
            'problem_count': len(problems),
            'unique_authors': 0,
            'topic_summary': {t: {'count': c, 'unique': len(topic_uniq[t])} for t, c in topic_counts.most_common()},
        }
        with open(args.save_report, 'w', encoding='utf-8') as f:
            json.dump(report_json, f, ensure_ascii=False, indent=2)
        print(f"Отчёт сохранён: {args.save_report}", file=sys.stderr)


if __name__ == '__main__':
    main()