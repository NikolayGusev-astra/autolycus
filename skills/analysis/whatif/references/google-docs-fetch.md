# Google Docs Fetch for Context Gathering

When a user provides a Google Docs link during Step 1 (context gathering):

```
# Export as plain text
curl -sL -A "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36" \
  "https://docs.google.com/document/d/DOC_ID/export?format=txt" \
  --connect-timeout 15 --max-time 45 > /tmp/source.txt

# Check size
wc -c /tmp/source.txt

# Read first N lines
head -200 /tmp/source.txt

# Read with read_file if file is in /root/wiki/raw/ or similar
read_file(path="/root/wiki/raw/source.txt", limit=200)
```

**Питфоллы:**
- Google может блокировать без User-Agent (`-A "Mozilla/5.0"` обязателен)
- Большие документы (500K+ строк) — экспорт может длиться 30+ секунд. `--max-time 45` минимум
- Первый запрос иногда возвращает 0 bytes (редирект на edit). Повторный — работает
- format=txt теряет: изображения, таблицы, форматирование, сноски. Для анализа сути — достаточно
- Если документ совсем не грузится — попробовать `/export?format=html` и парсить
- Сохранять в /tmp/ или /root/wiki/raw/ (если позже пригодится для wiki/HippoRAG)
