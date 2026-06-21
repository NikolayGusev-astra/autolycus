#!/usr/bin/env python3
"""
RAG Wiki embed script — инкрементальная индексация вики через Zvec.

Сканирует директорию вики, вычисляет эмбеддинги для новых/изменённых файлов
и добавляет их в Zvec коллекцию.
"""

import argparse
import hashlib
import json
import os
import sys
import time

ZVEC_BASE = os.path.expanduser("~/.cache/zvec")
ZVEC_COLLECTION = os.environ.get("RAG_ZVEC_PATH", os.path.join(ZVEC_BASE, "wiki"))
WIKI_DIR = os.environ.get("WIKI_DIR", "/root/wiki")


def _file_hash(path: str) -> str:
    """MD5 хеш файла для определения изменений."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _modified_since(path: str, since_ts: float) -> bool:
    """Был ли файл изменён после since_ts."""
    try:
        return os.path.getmtime(path) > since_ts
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description="RAG Wiki Zvec Embed")
    parser.add_argument("--full", action="store_true",
                        help="Полная переиндексация")
    parser.add_argument("--wiki-dir", default=WIKI_DIR,
                        help="Путь к вики")
    args = parser.parse_args()

    try:
        from zvec import Zvec
    except ImportError:
        import glob
        zvec_paths = glob.glob("/usr/local/lib/python*/dist-packages")
        zvec_paths += glob.glob("/root/.local/lib/python*/site-packages")
        for p in zvec_paths:
            if p not in sys.path:
                sys.path.insert(0, p)
        from zvec import Zvec

    if not os.path.isdir(args.wiki_dir):
        print(json.dumps({"error": f"wiki dir {args.wiki_dir} not exist"}))
        sys.exit(1)

    zv = Zvec(ZVEC_COLLECTION)
    t0 = time.time()

    # Собираем файлы
    files = []
    for root, dirs, filenames in os.walk(args.wiki_dir):
        # Пропускаем .git и скрытые
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in filenames:
            if fn.endswith((".md", ".txt", ".rst")):
                files.append(os.path.join(root, fn))

    # Индексируем
    indexed = 0
    skipped = 0
    errors = 0

    for fp in files:
        try:
            # Проверяем нужна ли индексация
            if not args.full:
                existing = zv.get_metadata(fp)  # или по хешу
                if existing:
                    cur_hash = _file_hash(fp)
                    if existing.get("hash") == cur_hash:
                        skipped += 1
                        continue

            with open(fp) as f:
                content = f.read()

            title = os.path.basename(fp).rsplit(".", 1)[0]
            relpath = os.path.relpath(fp, args.wiki_dir)
            file_hash = _file_hash(fp)

            # Добавляем/обновляем в Zvec
            zv.add(
                id=fp,
                text=content,
                metadata={
                    "title": title,
                    "path": relpath,
                    "hash": file_hash,
                    "source": "wiki",
                },
            )
            indexed += 1
        except Exception as e:
            errors += 1

    # Финализируем индекс
    zv.commit()
    elapsed = time.time() - t0

    print(json.dumps({
        "indexed": indexed,
        "skipped": skipped,
        "errors": errors,
        "total_files": len(files),
        "elapsed_s": round(elapsed, 2),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
