#!/usr/bin/env bash
set -euo pipefail

PYPROJECT="pyproject.toml"
INIT_PY="src/mcp_feedback_enhanced/__init__.py"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

current_version=$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$PYPROJECT" | head -1)
if [ -z "$current_version" ]; then
    echo -e "  ${RED}✗${NC} 无法从 ${PYPROJECT} 读取版本号"
    exit 1
fi

IFS='.' read -r major minor patch <<< "$current_version"
versions=("$((major + 1)).0.0" "${major}.$((minor + 1)).0" "${major}.${minor}.$((patch + 1))")

ARROW_RESULT=0

arrow_select() {
    local selected=$1
    shift
    local items=("$@")
    local count=${#items[@]}

    tput civis 2>/dev/null || true

    local i
    for ((i = 0; i < count; i++)); do
        if [ $i -eq $selected ]; then
            printf "  ${GREEN}❯${NC} %s\n" "${items[$i]}"
        else
            printf "    ${DIM}%s${NC}\n" "${items[$i]}"
        fi
    done

    while true; do
        read -rsn1 key
        if [ "$key" = $'\x1b' ]; then
            read -rsn2 key
            if [ "$key" = "[A" ] && [ $selected -gt 0 ]; then
                selected=$((selected - 1))
            elif [ "$key" = "[B" ] && [ $selected -lt $((count - 1)) ]; then
                selected=$((selected + 1))
            fi
        elif [ "$key" = "" ]; then
            break
        fi

        printf '\033[%dA' "$count"

        for ((i = 0; i < count; i++)); do
            printf '\033[2K'
            if [ $i -eq $selected ]; then
                printf "  ${GREEN}❯${NC} %s\n" "${items[$i]}"
            else
                printf "    ${DIM}%s${NC}\n" "${items[$i]}"
            fi
        done
    done

    tput cnorm 2>/dev/null || true
    ARROW_RESULT=$selected
}

echo ""
echo -e "  ${BOLD}gl-mcp-feedback Release${NC}"
echo ""
echo -e "  当前版本  ${CYAN}${current_version}${NC}"
echo ""
echo -e "  ${BOLD}选择新版本号${NC}  ${DIM}↑↓ 选择, Enter 确认${NC}"
echo ""

arrow_select 2 \
    "${versions[0]}  (major)" \
    "${versions[1]}  (minor)" \
    "${versions[2]}  (patch)"
new_version="${versions[$ARROW_RESULT]}"

echo ""
echo -e "  ${BOLD}确认发布?${NC}  ${CYAN}v${new_version}${NC}"
echo ""

arrow_select 0 "是" "否"

if [ $ARROW_RESULT -ne 0 ]; then
    echo ""
    echo -e "  ${YELLOW}已取消${NC}"
    echo ""
    exit 0
fi

echo ""

if [ "$(uname)" = "Darwin" ]; then
    sed -i '' "s/^version = \"${current_version}\"/version = \"${new_version}\"/" "$PYPROJECT"
    sed -i '' "s/__version__ = \"${current_version}\"/__version__ = \"${new_version}\"/" "$INIT_PY"
else
    sed -i "s/^version = \"${current_version}\"/version = \"${new_version}\"/" "$PYPROJECT"
    sed -i "s/__version__ = \"${current_version}\"/__version__ = \"${new_version}\"/" "$INIT_PY"
fi
echo -e "  ${GREEN}✔${NC} 版本号已更新"

uv lock
echo -e "  ${GREEN}✔${NC} uv.lock 已同步"

git add "$PYPROJECT" "$INIT_PY" uv.lock
git commit -m "chore: bump version to ${new_version}"
echo -e "  ${GREEN}✔${NC} 已提交"

git tag "v${new_version}"
echo -e "  ${GREEN}✔${NC} 已创建 tag ${CYAN}v${new_version}${NC}"

git push && git push --tags
echo -e "  ${GREEN}✔${NC} 已推送"

echo ""
echo -e "  ${GREEN}${BOLD}发布完成${NC}  ${CYAN}v${new_version}${NC}"
echo -e "  ${DIM}GitHub Actions 将自动构建并发布到 PyPI${NC}"

echo ""
