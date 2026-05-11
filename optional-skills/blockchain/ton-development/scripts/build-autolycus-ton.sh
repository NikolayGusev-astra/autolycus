#!/bin/bash
# Сборка Docker-образа Автолика с предустановленным Acton CLI
# Использование: ./build-autolycus-ton.sh

set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-autolycus-ton}"
TAG="${TAG:-latest}"
CONTEXT="${1:-.}"

echo "🔨 Сборка образа $IMAGE_NAME:$TAG из $CONTEXT..."
echo "   Acton CLI будет установлен в образ через Dockerfile"

docker build \
  -t "$IMAGE_NAME:$TAG" \
  -f "$CONTEXT/Dockerfile" \
  "$CONTEXT"

echo "✅ Готово!"
echo ""
echo "Запуск:"
echo "  docker run --rm -it \\"
echo "    -v \"\$PWD:/workspace\" \\"
echo "    -w /workspace \\"
echo "    $IMAGE_NAME:$TAG acton --version"
echo ""
echo "Проверка Acton внутри контейнера:"
echo "  docker run --rm $IMAGE_NAME:$TAG acton --version"
