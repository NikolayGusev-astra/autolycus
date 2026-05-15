#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# portable/install.sh — установка DevOps инструментария (швейцарский нож)
#
# Использование:
#   bash portable/install.sh            # стандартная установка
#   ALL_PROXY=socks5://127.0.0.1:2081 bash portable/install.sh  # через прокси
#   bash portable/install.sh --venv-only  # только Python окружение
#   bash portable/install.sh --bin-only   # только бинарники
#
# Инструменты устанавливаются в:
#   portable/bin/   — статические бинарники
#   portable/venv/  — Python окружение (ansible + pyone)
#
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

PORTABLE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="$PORTABLE_DIR/portable/bin"
VENV_DIR="$PORTABLE_DIR/portable/venv"
VERSIONS_FILE="$PORTABLE_DIR/portable/versions.yaml"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

info()  { echo -e "${GREEN}✓${NC} $1"; }
warn()  { echo -e "${YELLOW}⚠${NC} $1"; }
err()   { echo -e "${RED}✗${NC} $1"; }
step()  { echo -e "\n${YELLOW}═══ $1 ═══${NC}"; }

# ── Определяем прокси ────────────────────────────────────────────────────────
# Порядок: ALL_PROXY > http_proxy > ansible.cfg proxy > прямой доступ
PROXY="${ALL_PROXY:-${http_proxy:-${HTTP_PROXY:-}}}"
CURL_OPTS=("-sL" "--connect-timeout" "10")
if [ -n "$PROXY" ]; then
    CURL_OPTS+=("-x" "$PROXY")
    info "Использую прокси: $PROXY"
else
    info "Прямой доступ к сети"
fi

# ── Проверяем зависимости ────────────────────────────────────────────────────
for cmd in curl tar unzip python3; do
    if ! command -v "$cmd" &>/dev/null; then
        err "Не найден $cmd — установите его через apt: sudo apt install $cmd"
        exit 1
    fi
done

# ── Читаем версии ────────────────────────────────────────────────────────────
read_yaml() {
    python3 -c "
import yaml, sys
with open('$VERSIONS_FILE') as f:
    d = yaml.safe_load(f)
print(d.get('$1', {}).get('$2', ''))
"
}

# ── Функция загрузки ──────────────────────────────────────────────────────────
download_to() {
    local url="$1"
    local dest="$2"
    if [ -f "$dest" ]; then
        warn "Пропускаю (уже есть): $(basename "$dest")"
        return 0
    fi
    echo "  ↓ $url"
    mkdir -p "$(dirname "$dest")"
    curl "${CURL_OPTS[@]}" -o "$dest" "$url" || {
        err "Не удалось загрузить $(basename "$dest")"
        return 1
    }
}

# ── install.sh --bin-only ─────────────────────────────────────────────────────
install_binaries() {
    step "Установка бинарников в portable/bin/"

    # terraform
    local tf_ver; tf_ver=$(read_yaml terraform version)
    if [ -n "$tf_ver" ]; then
        local tf_url; tf_url=$(read_yaml terraform url)
        local tmp_tf="/tmp/terraform-${tf_ver}.zip"
        download_to "$tf_url" "$tmp_tf"
        if [ -f "$tmp_tf" ]; then
            unzip -o "$tmp_tf" terraform -d "$BIN_DIR" 2>/dev/null
            chmod +x "$BIN_DIR/terraform"
            info "terraform ${tf_ver}: $BIN_DIR/terraform"
            rm -f "$tmp_tf"
        fi
    fi

    # fd
    local fd_ver; fd_ver=$(read_yaml fd version)
    if [ -n "$fd_ver" ]; then
        local fd_url; fd_url=$(read_yaml fd url)
        local tmp_fd="/tmp/fd-${fd_ver}.tar.gz"
        download_to "$fd_url" "$tmp_fd"
        if [ -f "$tmp_fd" ]; then
            local dir_name="fd-v${fd_ver}-x86_64-unknown-linux-musl"
            tar xzf "$tmp_fd" -C /tmp "$dir_name/fd"
            mv "/tmp/$dir_name/fd" "$BIN_DIR/fd"
            chmod +x "$BIN_DIR/fd"
            rm -rf "/tmp/$dir_name" "$tmp_fd"
            info "fd ${fd_ver}: $BIN_DIR/fd"
        fi
    fi

    # ripgrep
    local rg_ver; rg_ver=$(read_yaml rg version)
    if [ -n "$rg_ver" ]; then
        local rg_url; rg_url=$(read_yaml rg url)
        local tmp_rg="/tmp/rg-${rg_ver}.tar.gz"
        download_to "$rg_url" "$tmp_rg"
        if [ -f "$tmp_rg" ]; then
            local dir_name="ripgrep-${rg_ver}-x86_64-unknown-linux-musl"
            tar xzf "$tmp_rg" -C /tmp "$dir_name/rg"
            mv "/tmp/$dir_name/rg" "$BIN_DIR/rg"
            chmod +x "$BIN_DIR/rg"
            rm -rf "/tmp/$dir_name" "$tmp_rg"
            info "rg ${rg_ver}: $BIN_DIR/rg"
        fi
    fi

    # gh
    local gh_ver; gh_ver=$(read_yaml gh version)
    if [ -n "$gh_ver" ]; then
        local gh_url; gh_url=$(read_yaml gh url)
        local tmp_gh="/tmp/gh-${gh_ver}.tar.gz"
        download_to "$gh_url" "$tmp_gh"
        if [ -f "$tmp_gh" ]; then
            local dir_name="gh_${gh_ver}_linux_amd64"
            tar xzf "$tmp_gh" -C /tmp "$dir_name/bin/gh"
            mv "/tmp/$dir_name/bin/gh" "$BIN_DIR/gh"
            chmod +x "$BIN_DIR/gh"
            rm -rf "/tmp/$dir_name" "$tmp_gh"
            info "gh ${gh_ver}: $BIN_DIR/gh"
        fi
    fi

    # jq (single binary)
    local jq_ver; jq_ver=$(read_yaml jq version)
    if [ -n "$jq_ver" ]; then
        local jq_url; jq_url=$(read_yaml jq url)
        download_to "$jq_url" "$BIN_DIR/jq"
        chmod +x "$BIN_DIR/jq"
        info "jq ${jq_ver}: $BIN_DIR/jq"
    fi

    # yq (single binary)
    local yq_ver; yq_ver=$(read_yaml yq version)
    if [ -n "$yq_ver" ]; then
        local yq_url; yq_url=$(read_yaml yq url)
        download_to "$yq_url" "$BIN_DIR/yq"
        chmod +x "$BIN_DIR/yq"
        info "yq ${yq_ver}: $BIN_DIR/yq"
    fi
}

# ── install.sh --venv-only ────────────────────────────────────────────────────
install_venv() {
    step "Создание Python окружения в portable/venv/"

    if [ -f "$VENV_DIR/bin/python3" ]; then
        warn "venv уже существует, пропускаю создание"
    else
        python3 -m venv "$VENV_DIR"
        info "venv создан: $VENV_DIR"
    fi

    # Активируем
    source "$VENV_DIR/bin/activate"

    # Устанавливаем pip-пакеты
    local pip_pkgs
    pip_pkgs=$(python3 -c "
import yaml
with open('$VERSIONS_FILE') as f:
    d = yaml.safe_load(f)
print(' '.join(d.get('ansible', {}).get('pip_packages', [])))
")

    if [ -n "$pip_pkgs" ]; then
        step "Установка pip-пакетов: $pip_pkgs"
        pip install --quiet --upgrade pip 2>/dev/null || true
        pip install --quiet $pip_pkgs
        info "pip-пакеты установлены"
    fi

    # Устанавливаем Ansible коллекции
    local collections_json
    collections_json=$(python3 -c "
import yaml, json
with open('$VERSIONS_FILE') as f:
    d = yaml.safe_load(f)
print(json.dumps(d.get('ansible', {}).get('collections', [])))
")

    if [ "$collections_json" != "[]" ]; then
        step "Установка Ansible коллекций"
        local req_file="/tmp/ansible-requirements-$$.yml"
        python3 -c "
import yaml, json
with open('$VERSIONS_FILE') as f:
    d = yaml.safe_load(f)
req = {'collections': []}
for c in d.get('ansible', {}).get('collections', []):
    entry = {'name': c['name'], 'version': c['version']}
    entry['source'] = 'published' if c['name'].startswith('astra.') else None
    req['collections'].append(entry if entry['source'] else {'name': c['name'], 'version': c['version']})
with open('$req_file', 'w') as f:
    yaml.dump(req, f)
" 2>/dev/null
        if [ -f "$req_file" ]; then
            ansible-galaxy collection install -r "$req_file" 2>&1 || warn "Некоторые коллекции не установились (проверьте токен Automation Hub)"
            rm -f "$req_file"
        fi
        info "Коллекции установлены"
    fi

    deactivate 2>/dev/null || true
}

# ── MAIN ─────────────────────────────────────────────────────────────────────
main() {
    echo ""
    echo "╔══════════════════════════════════════════════╗"
    echo "║   portable — DevOps Swiss Army Knife        ║"
    echo "║   autolycus/devops toolchain installer       ║"
    echo "╚══════════════════════════════════════════════╝"
    echo ""

    local do_bin=true
    local do_venv=true

    for arg in "$@"; do
        case "$arg" in
            --bin-only) do_venv=false ;;
            --venv-only) do_bin=false ;;
            --help|-h)
                echo "Использование: bash portable/install.sh [--bin-only|--venv-only]"
                echo "  --bin-only   — только бинарники (terraform, fd, rg, gh, jq, yq)"
                echo "  --venv-only  — только Python venv (ansible + pyone)"
                exit 0
                ;;
        esac
    done

    if $do_bin; then install_binaries; fi
    if $do_venv; then install_venv; fi

    step "Готово!"
    echo ""
    echo "  portable/bin/  — $(ls "$BIN_DIR" 2>/dev/null | wc -l) бинарников"
    echo "  portable/venv/ — $(ls "$VENV_DIR/bin/" 2>/dev/null | wc -l) скриптов"
    echo ""
    echo "  Чтобы добавить в PATH:"
    echo "    export PATH=\"$PORTABLE_DIR/portable/bin:\$PATH\""
    echo "    source $VENV_DIR/bin/activate"
}

main "$@"
