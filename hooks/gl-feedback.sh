#!/bin/bash
# gl-mcp-feedback 的 Cursor 钩子，用法：gl-feedback.sh <模式>
#
#   pre   preToolUse，只挂在 interactive_feedback 上：
#         先删掉入参里的 title 和 conversation_id（服务端不认 title，对话标识只认钩子填的），
#         再把本次对话的标识写进 conversation_id。
#         只给 GL_INJECT_ROOT（默认是本仓库）项目里的对话填，其他项目只做删除。
#   post  postToolUse 和 postToolUseFailure，挂在所有工具上：
#         这个对话有排队的消息就向服务端取走，用 additional_context 附在工具结果后面交给 AI。
#   log   sessionStart、sessionEnd、stop：只把事件写进 hook-events.log。
#
# post 在每次工具调用结束时都会触发，没有排队消息时要几毫秒返回：
# 只从输入开头取对话标识，看标记文件 $GL_FEEDBACK_HOME/queue/<对话标识> 在不在，不在就直接返回。
# 标记文件两行：房间的地址、写它的服务进程号；进程已经不在了，说明标记是过期的，删掉。
#
# AwaitShell 等后台任务时，Cursor 每秒几次对终端输出文件发起内部 Read，每次都触发 postToolUse，
# 这些事件返回的 additional_context 会被丢弃；这时取走消息，消息就丢了，所以认出它们、不取，留给下一次真正的工具调用。
#
# 任何异常都放行：输出合法 JSON，退出码 0（preToolUse 输出非法 JSON 会拦住调用）。
#
# 环境变量：
#   GL_FEEDBACK_HOME  标记文件、图片和事件日志所在目录，默认 ~/.cache/gl-mcp-feedback，服务端读的是同一个变量
#   GL_INJECT_ROOT    pre 要填对话标识的项目目录，默认是本脚本所在的仓库
#   GL_JQ             jq 的位置，默认 PATH 里的 jq，找不到用 /usr/bin/jq

umask 077

FEEDBACK_HOME="${GL_FEEDBACK_HOME:-$HOME/.cache/gl-mcp-feedback}"
MODE="${1:-}"

# 事件日志超过这个大小（字节）就换新文件，旧的留一份
LOG_MAX_BYTES=1048576
# 对话标识一般在输入开头，工具输出特别大时不必整段扫描
HEAD_CHARS=65536
# 工具名和入参紧跟在对话标识后面，认内部轮询只看开头这一小段
POLL_SCAN_CHARS=4096
# 对话标识的合法格式，和服务端房间标识的规则一致
ID_PATTERN='[0-9A-Za-z_-]{1,128}'

input=$(cat)
response='{}'

# post 的热路径用不上 jq 和仓库位置，要用时才找
find_jq() {
    if [ -n "${GL_JQ:-}" ]; then
        printf '%s' "$GL_JQ"
    else
        command -v jq || printf '%s' /usr/bin/jq
    fi
}

inject_root() {
    if [ -n "${GL_INJECT_ROOT:-}" ]; then
        printf '%s' "$GL_INJECT_ROOT"
    else
        local script_dir=.
        case "$0" in
            */*) script_dir=${0%/*} ;;
        esac
        (cd "$script_dir/.." 2>/dev/null && pwd)
    fi
}

run_pre() {
    response='{"permission":"allow"}'
    local jq_bin root rewritten
    jq_bin=$(find_jq)
    root=$(inject_root)
    rewritten=$(printf '%s' "$input" | "$jq_bin" -c --arg root "$root" '
        (if (.tool_input | type) == "string" then (.tool_input | try fromjson catch null) else .tool_input end) as $ti
        | (.conversation_id // "") as $cid
        | if ($ti | type) != "object" then empty
          else
              ($ti | del(.title, .conversation_id)) as $clean
              | if ($cid | test("^[0-9A-Za-z_-]{1,128}$")) and ((.workspace_roots // []) | any(. == $root))
                then {permission: "allow", updated_input: ($clean + {conversation_id: $cid})}
                elif $clean != $ti then {permission: "allow", updated_input: $clean}
                else empty
                end
          end
    ' 2>/dev/null)
    if [ -n "$rewritten" ]; then
        response=$rewritten
    fi
}

# 从输入里取对话标识：先在开头一段里用正则找，找不到再用 jq 完整解析
extract_conversation_id() {
    local head_part=${input:0:$HEAD_CHARS}
    local pattern="\"conversation_id\"[[:space:]]*:[[:space:]]*\"($ID_PATTERN)\""
    if [[ $head_part =~ $pattern ]]; then
        printf '%s' "${BASH_REMATCH[1]}"
        return 0
    fi

    local parsed
    parsed=$(printf '%s' "$input" | "$(find_jq)" -r '.conversation_id // empty' 2>/dev/null) || return 1
    [[ $parsed =~ ^$ID_PATTERN$ ]] || return 1
    printf '%s' "$parsed"
}

# 是不是 AwaitShell 轮询终端输出文件产生的事件：工具是 Read，读的是 .../terminals/<编号>.txt
is_terminal_poll() {
    local head_part=${input:0:$POLL_SCAN_CHARS}
    local name_pattern='"tool_name"[[:space:]]*:[[:space:]]*"Read"'
    local path_pattern='"file_path"[[:space:]]*:[[:space:]]*"[^"]*/terminals/[^"/]*\.txt"'
    [[ $head_part =~ $name_pattern ]] && [[ $head_part =~ $path_pattern ]]
}

run_post() {
    local conversation_id marker url pid result
    conversation_id=$(extract_conversation_id) || return 0

    marker="$FEEDBACK_HOME/queue/$conversation_id"
    [ -f "$marker" ] || return 0
    is_terminal_poll && return 0
    { read -r url && read -r pid; } 2>/dev/null <"$marker"
    [ -n "$url" ] || return 0
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$marker"
        return 0
    fi

    result=$(curl -sf --noproxy '*' --proto '=http' --connect-timeout 1 --max-time 5 \
        -X POST "$url/api/queue/take" 2>/dev/null) || return 0
    case "$result" in
        '{'*) response=$result ;;
    esac
}

run_log() {
    local log_file="$FEEDBACK_HOME/hook-events.log"
    mkdir -p "$FEEDBACK_HOME" 2>/dev/null || return 0
    if [ -f "$log_file" ] && [ "$(wc -c <"$log_file")" -gt "$LOG_MAX_BYTES" ]; then
        mv -f "$log_file" "$log_file.1"
    fi
    printf '%s' "$input" | "$(find_jq)" -c --arg ts "$(date '+%Y-%m-%dT%H:%M:%S%z')" '
        {
            ts: $ts,
            event: .hook_event_name,
            conversation_id,
            session_id,
            generation_id,
            status,
            reason,
            final_status,
            loop_count,
            duration_ms,
            is_background_agent,
            composer_mode,
            cursor_version,
            workspace_roots,
            transcript_path
        }
        | with_entries(select(.value != null))
    ' >>"$log_file" 2>/dev/null
}

case "$MODE" in
    pre) run_pre ;;
    post) run_post ;;
    log) run_log ;;
esac

printf '%s\n' "$response"
exit 0
